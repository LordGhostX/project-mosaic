import os
import re
import time
from datetime import date, datetime, timezone
from functools import lru_cache
from typing import Iterable

import clickhouse_connect
import pandas as pd

CLICKHOUSE_HOST = os.getenv("CLICKHOUSE_HOST", "localhost")
CLICKHOUSE_PORT = int(os.getenv("CLICKHOUSE_PORT", "8123"))
CLICKHOUSE_USER = os.getenv("CLICKHOUSE_USER", "default")
CLICKHOUSE_PASSWORD = os.getenv("CLICKHOUSE_PASSWORD", "")
CLICKHOUSE_DATABASE = os.getenv("CLICKHOUSE_DATABASE", "hyperliquid")
FILLS_TABLE = os.getenv("CLICKHOUSE_FILLS_TABLE", "fills")


def _ident(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value):
        raise ValueError(f"Bad SQL identifier: {value!r}")
    return value


def _fills_table() -> str:
    return _ident(FILLS_TABLE)


def _as_utc_datetime(value: str | date | datetime) -> datetime:
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, date):
        dt = datetime.combine(value, datetime.min.time())
    else:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))

    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)

    return dt.astimezone(timezone.utc)


def _epoch_ms(value: str | date | datetime) -> int:
    return int(_as_utc_datetime(value).timestamp() * 1000)


def _format_epoch_ms(value: int) -> str:
    return datetime.fromtimestamp(value / 1000, tz=timezone.utc).strftime(
        "%Y-%m-%d %H:%M:%S"
    )


def _result_df(result):
    return pd.DataFrame(result.result_rows, columns=result.column_names)


@lru_cache(maxsize=1)
def get_client():
    return clickhouse_connect.get_client(
        host=CLICKHOUSE_HOST,
        port=CLICKHOUSE_PORT,
        username=CLICKHOUSE_USER,
        password=CLICKHOUSE_PASSWORD,
        database=CLICKHOUSE_DATABASE,
        autogenerate_session_id=False,
    )


def query_df(sql: str, parameters: dict | None = None):
    result = get_client().query(sql, parameters=parameters or {})
    return _result_df(result)


def get_fills_time_bounds():
    sql = f"""
        SELECT
            min(time) AS first_fill_ms,
            max(time) AS last_fill_ms,
            count() AS fills
        FROM {_fills_table()}
    """
    row = get_client().query(sql).result_rows[0]
    return {
        "first_fill_at": _format_epoch_ms(row[0]),
        "last_fill_at": _format_epoch_ms(row[1]),
        "fills": int(row[2]),
    }


def get_profitable_active_traders(
    rebalance_at: str | date | datetime,
    *,
    active_days: int = 7,
    history_days: int = 30,
    min_active_fills: int = 1,
    min_history_fills: int = 1,
    min_net_pnl: float = 0,
    asset_class: str | None = "perp",
    limit: int = 0,
):
    """Return traders active before a rebalance whose trailing history is profitable.

    Profitability is realized PnL after fees: sum(closed_pnl) - sum(fee).
    """

    rebalance_ms = _epoch_ms(rebalance_at)
    active_start_ms = rebalance_ms - active_days * 24 * 60 * 60 * 1000
    history_start_ms = rebalance_ms - history_days * 24 * 60 * 60 * 1000
    if limit < 0:
        raise ValueError("limit must be >= 0")
    limit_clause = "" if limit == 0 else "LIMIT {limit:UInt32}"

    sql = f"""
        WITH
            active_traders AS (
                SELECT address
                FROM {_fills_table()}
                WHERE time >= {{active_start_ms:UInt64}}
                  AND time < {{rebalance_ms:UInt64}}
                  AND ({{asset_class:String}} = '' OR asset_class = {{asset_class:String}})
                GROUP BY address
                HAVING count() >= {{min_active_fills:UInt32}}
            )
        SELECT
            address,
            min(toDateTime(intDiv(time, 1000), 'UTC')) AS first_fill_at,
            max(toDateTime(intDiv(time, 1000), 'UTC')) AS last_fill_at,
            count() AS history_fills,
            toFloat64(history_fills) / {{history_days:Float64}} AS history_fills_per_day,
            countIf(time >= {{active_start_ms:UInt64}}) AS active_period_fills,
            uniqExact(intDiv(time, 86400000)) AS active_history_days,
            uniqExact(coin) AS coins_traded,
            uniqExactIf(coin, closed_pnl != 0) AS coins_with_realized_pnl,
            sum(closed_pnl) AS total_closed_pnl,
            sum(fee) AS fees,
            total_closed_pnl - fees AS net_pnl,
            sum(px * sz) AS gross_notional,
            if(history_fills = 0, 0, {{history_days:Float64}} * 86400 / history_fills) AS avg_seconds_between_fills,
            if(history_fills = 0, 0, toFloat64(gross_notional) / history_fills) AS avg_fill_notional,
            if(gross_notional = 0, 0, toFloat64(fees) / toFloat64(gross_notional)) AS fee_rate_on_notional,
            if(gross_notional = 0, 0, toFloat64(net_pnl) / toFloat64(gross_notional)) AS net_pnl_per_notional,
            sumIf(px * sz, side = 'B') AS buy_notional,
            sumIf(px * sz, side = 'A') AS sell_notional,
            abs(buy_notional - sell_notional) AS abs_side_imbalance_notional,
            countIf(closed_pnl != 0) AS realized_pnl_fills,
            countIf(closed_pnl > 0) AS winning_realized_fills,
            countIf(closed_pnl < 0) AS losing_realized_fills,
            if(
                winning_realized_fills + losing_realized_fills = 0,
                0,
                toFloat64(winning_realized_fills) / (winning_realized_fills + losing_realized_fills)
            ) AS realized_win_rate,
            sumIf(closed_pnl, closed_pnl > 0) AS gross_realized_profit,
            abs(sumIf(closed_pnl, closed_pnl < 0)) AS gross_realized_loss,
            if(gross_realized_loss = 0, NULL, toFloat64(gross_realized_profit) / toFloat64(gross_realized_loss)) AS realized_profit_factor,
            countIf(crossed) AS crossed_fills,
            if(history_fills = 0, 0, toFloat64(crossed_fills) / history_fills) AS crossed_fill_rate,
            countIf(twap_id IS NOT NULL) AS twap_fills,
            countIf(
                positionCaseInsensitive(dir, 'Open') > 0
                OR dir IN ('Long > Short', 'Short > Long')
            ) AS open_fills,
            countIf(
                positionCaseInsensitive(dir, 'Close') > 0
                OR dir IN ('Long > Short', 'Short > Long')
            ) AS close_fills,
            countIf(dir IN ('Long > Short', 'Short > Long')) AS flip_fills,
            if(history_fills = 0, 0, toFloat64(close_fills) / history_fills) AS close_fill_rate,
            if(history_fills = 0, 0, toFloat64(flip_fills) / history_fills) AS flip_fill_rate,
            max(abs(start_position)) AS max_abs_start_position,
            countDistinct(oid) AS orders,
            toFloat64(orders) / {{history_days:Float64}} AS orders_per_day,
            countDistinct(tid) AS trades,
            toFloat64(trades) / {{history_days:Float64}} AS trades_per_day
        FROM {_fills_table()}
        WHERE time >= {{history_start_ms:UInt64}}
          AND time < {{rebalance_ms:UInt64}}
          AND address IN (SELECT address FROM active_traders)
          AND ({{asset_class:String}} = '' OR asset_class = {{asset_class:String}})
        GROUP BY address
        HAVING history_fills >= {{min_history_fills:UInt32}}
           AND net_pnl > {{min_net_pnl:Float64}}
        ORDER BY net_pnl DESC
        {limit_clause}
    """

    started_at = time.perf_counter()
    df = query_df(
        sql,
        {
            "active_start_ms": active_start_ms,
            "history_start_ms": history_start_ms,
            "rebalance_ms": rebalance_ms,
            "min_active_fills": min_active_fills,
            "min_history_fills": min_history_fills,
            "min_net_pnl": min_net_pnl,
            "history_days": history_days,
            "asset_class": asset_class or "",
            "limit": limit,
        },
    )
    df["query_elapsed_seconds"] = round(time.perf_counter() - started_at, 3)
    return df


def get_trader_history_fills(
    addresses: Iterable[str],
    rebalance_at: str | date | datetime,
    *,
    history_days: int = 30,
    asset_class: str | None = "perp",
    limit: int = 50_000,
):
    address_list = list(addresses)
    if not address_list:
        return pd.DataFrame()

    rebalance_ms = _epoch_ms(rebalance_at)
    history_start_ms = rebalance_ms - history_days * 24 * 60 * 60 * 1000

    sql = f"""
        SELECT
            address,
            toDateTime(intDiv(time, 1000), 'UTC') AS fill_at,
            coin,
            asset_class,
            px,
            sz,
            side,
            start_position,
            dir,
            closed_pnl,
            fee,
            closed_pnl - fee AS net_pnl,
            hash,
            oid,
            tid,
            crossed,
            fee_token,
            twap_id
        FROM {_fills_table()}
        WHERE time >= {{history_start_ms:UInt64}}
          AND time < {{rebalance_ms:UInt64}}
          AND has({{addresses:Array(String)}}, address)
          AND ({{asset_class:String}} = '' OR asset_class = {{asset_class:String}})
        ORDER BY address, time, tid
        LIMIT {{limit:UInt32}}
    """

    return query_df(
        sql,
        {
            "addresses": address_list,
            "history_start_ms": history_start_ms,
            "rebalance_ms": rebalance_ms,
            "asset_class": asset_class or "",
            "limit": limit,
        },
    )

import os
import re
import time
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
from typing import Iterable

import clickhouse_connect
import pandas as pd
from clickhouse_connect.driver.external import ExternalData

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


def query_df(
    sql: str,
    parameters: dict | None = None,
    external_data: ExternalData | None = None,
):
    result = get_client().query(
        sql,
        parameters=parameters or {},
        external_data=external_data,
    )
    return _result_df(result)


def _addresses_external_data(addresses: Iterable[str]) -> ExternalData:
    data = "".join(f"{address}\n" for address in addresses).encode()
    return ExternalData(
        data=data,
        file_name="candidate_addresses.csv",
        fmt="CSV",
        structure="address String",
    )


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
    min_history_fills: int = 1,
    min_net_pnl: float = 0,
    asset_class: str | None = "perp",
):
    rebalance_ms = _epoch_ms(rebalance_at)
    active_start_ms = rebalance_ms - active_days * 24 * 60 * 60 * 1000
    history_start_ms = rebalance_ms - history_days * 24 * 60 * 60 * 1000

    sql = f"""
        WITH
            active_traders AS (
                SELECT address
                FROM {_fills_table()}
                WHERE time >= {{active_start_ms:UInt64}}
                  AND time < {{rebalance_ms:UInt64}}
                  AND ({{asset_class:String}} = '' OR asset_class = {{asset_class:String}})
                GROUP BY address
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
            toFloat64(orders) / {{history_days:Float64}} AS orders_per_day
        FROM {_fills_table()}
        WHERE time >= {{history_start_ms:UInt64}}
          AND time < {{rebalance_ms:UInt64}}
          AND address IN (SELECT address FROM active_traders)
          AND ({{asset_class:String}} = '' OR asset_class = {{asset_class:String}})
        GROUP BY address
        HAVING history_fills >= {{min_history_fills:UInt32}}
           AND net_pnl > {{min_net_pnl:Float64}}
    """

    started_at = time.perf_counter()
    df = query_df(
        sql,
        {
            "active_start_ms": active_start_ms,
            "history_start_ms": history_start_ms,
            "rebalance_ms": rebalance_ms,
            "min_history_fills": min_history_fills,
            "min_net_pnl": min_net_pnl,
            "history_days": history_days,
            "asset_class": asset_class or "",
        },
    )
    df["query_elapsed_seconds"] = round(time.perf_counter() - started_at, 3)
    return df


def evaluate_candidates(
    candidates: pd.DataFrame,
    rebalance_at: str | date | datetime,
    *,
    history_days: int = 30,
    forward_days: int = 7,
    asset_class: str | None = "perp",
):
    if candidates.empty:
        return candidates.copy()

    if "address" not in candidates.columns:
        raise ValueError("candidates must include an address column")

    addresses = candidates["address"].dropna().drop_duplicates().tolist()
    if not addresses:
        return candidates.copy()

    rebalance_dt = _as_utc_datetime(rebalance_at)
    rebalance_ms = _epoch_ms(rebalance_dt)
    history_start_ms = rebalance_ms - history_days * 24 * 60 * 60 * 1000
    forward_end_ms = rebalance_ms + forward_days * 24 * 60 * 60 * 1000

    sql = f"""
        SELECT
            address,
            if(time < {{rebalance_ms:UInt64}}, 'history', 'forward') AS period,
            toDate(toDateTime(intDiv(time, 1000), 'UTC')) AS day,
            coin,
            count() AS fills,
            countDistinct(oid) AS orders,
            sum(closed_pnl) AS total_closed_pnl,
            sum(fee) AS fees,
            sum(closed_pnl) - sum(fee) AS net_pnl,
            sum(px * sz) AS gross_notional,
            countIf(closed_pnl != 0) AS realized_pnl_fills,
            countIf(closed_pnl > 0) AS winning_realized_fills,
            countIf(closed_pnl < 0) AS losing_realized_fills,
            sumIf(closed_pnl, closed_pnl > 0) AS gross_realized_profit,
            abs(sumIf(closed_pnl, closed_pnl < 0)) AS gross_realized_loss,
            countIf(crossed) AS crossed_fills,
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
            max(abs(start_position)) AS max_abs_start_position
        FROM {_fills_table()}
        WHERE time >= {{history_start_ms:UInt64}}
          AND time < {{forward_end_ms:UInt64}}
          AND address IN (SELECT address FROM candidate_addresses)
          AND ({{asset_class:String}} = '' OR asset_class = {{asset_class:String}})
        GROUP BY address, period, day, coin
    """

    started_at = time.perf_counter()
    grain = query_df(
        sql,
        {
            "history_start_ms": history_start_ms,
            "rebalance_ms": rebalance_ms,
            "forward_end_ms": forward_end_ms,
            "asset_class": asset_class or "",
        },
        external_data=_addresses_external_data(addresses),
    )
    elapsed = round(time.perf_counter() - started_at, 3)

    if grain.empty:
        evaluated = candidates.copy()
        evaluated["evaluation_elapsed_seconds"] = elapsed
        return evaluated

    numeric_columns = [
        "fills",
        "orders",
        "total_closed_pnl",
        "fees",
        "net_pnl",
        "gross_notional",
        "realized_pnl_fills",
        "winning_realized_fills",
        "losing_realized_fills",
        "gross_realized_profit",
        "gross_realized_loss",
        "crossed_fills",
        "twap_fills",
        "open_fills",
        "close_fills",
        "flip_fills",
        "max_abs_start_position",
    ]
    for column in numeric_columns:
        grain[column] = pd.to_numeric(grain[column])

    grain["day"] = pd.to_datetime(grain["day"]).apply(lambda value: value.date())

    base = candidates.drop_duplicates("address").copy()
    candidate_numeric_columns = [
        "gross_notional",
        "net_pnl_per_notional",
        "active_history_days",
        "coins_traded",
    ]
    for column in candidate_numeric_columns:
        if column in base.columns:
            base[column] = pd.to_numeric(base[column])

    history_grain = pd.DataFrame(grain.loc[grain["period"] == "history"]).copy()
    forward_grain = pd.DataFrame(grain.loc[grain["period"] == "forward"]).copy()
    evaluated = base.copy()
    notional_by_address = pd.DataFrame(
        {
            "address": evaluated["address"],
            "score_gross_notional": evaluated["gross_notional"].replace(0, pd.NA),
        }
    )

    history_days_index = [
        rebalance_dt.date() - timedelta(days=history_days - offset)
        for offset in range(history_days)
    ]
    forward_days_index = [
        rebalance_dt.date() + timedelta(days=offset) for offset in range(forward_days)
    ]

    def daily_frame(source: pd.DataFrame, days: list[date]) -> pd.DataFrame:
        by_day = source.groupby(["address", "day"], as_index=True).agg(
            daily_net_pnl=("net_pnl", "sum"),
            daily_gross_notional=("gross_notional", "sum"),
            daily_fills=("fills", "sum"),
        )
        full_index = pd.MultiIndex.from_product(
            [addresses, days], names=["address", "day"]
        )
        daily = by_day.reindex(full_index, fill_value=0).reset_index()
        daily = daily.merge(notional_by_address, on="address", how="left")
        daily["daily_return_on_history_notional"] = (
            daily["daily_net_pnl"] / daily["score_gross_notional"]
        ).fillna(0)
        daily["cum_net_pnl"] = daily.groupby("address")["daily_net_pnl"].cumsum()
        daily["running_peak_net_pnl"] = (
            daily.groupby("address")["cum_net_pnl"].cummax().clip(lower=0)
        )
        daily["drawdown"] = daily["cum_net_pnl"] - daily["running_peak_net_pnl"]
        return daily

    history_daily = daily_frame(history_grain, history_days_index)
    forward_daily = daily_frame(forward_grain, forward_days_index)

    score_metrics = history_daily.groupby("address", as_index=False).agg(
        score_daily_net_pnl_mean=("daily_net_pnl", "mean"),
        score_daily_net_pnl_std=("daily_net_pnl", "std"),
        score_positive_day_rate=("daily_net_pnl", lambda values: (values > 0).mean()),
        score_active_day_rate=("daily_fills", lambda values: (values > 0).mean()),
        score_active_days=("daily_fills", lambda values: (values > 0).sum()),
        score_max_drawdown=("drawdown", lambda values: abs(values.min())),
        score_return_mean=("daily_return_on_history_notional", "mean"),
        score_return_std=("daily_return_on_history_notional", "std"),
        score_return_skew=("daily_return_on_history_notional", "skew"),
        score_return_kurtosis=("daily_return_on_history_notional", "kurt"),
    )
    history_downside = history_daily[
        history_daily["daily_return_on_history_notional"] < 0
    ]
    history_downside_std = pd.DataFrame(
        history_downside.groupby("address")["daily_return_on_history_notional"]
        .std()
        .to_frame("score_downside_return_std")
        .reset_index()
    )
    score_metrics = score_metrics.merge(history_downside_std, on="address", how="left")
    score_metrics["score_sharpe"] = (
        score_metrics["score_return_mean"]
        / score_metrics["score_return_std"].replace(0, pd.NA)
        * (365**0.5)
    )
    score_metrics["score_sortino"] = (
        score_metrics["score_return_mean"]
        / score_metrics["score_downside_return_std"].replace(0, pd.NA)
        * (365**0.5)
    )

    history_totals = history_grain.groupby("address", as_index=False).agg(
        hist_fills=("fills", "sum"),
        hist_orders=("orders", "sum"),
        hist_net_pnl=("net_pnl", "sum"),
        hist_gross_notional=("gross_notional", "sum"),
        hist_fees=("fees", "sum"),
        hist_winning_realized_fills=("winning_realized_fills", "sum"),
        hist_losing_realized_fills=("losing_realized_fills", "sum"),
        hist_gross_realized_profit=("gross_realized_profit", "sum"),
        hist_gross_realized_loss=("gross_realized_loss", "sum"),
        hist_crossed_fills=("crossed_fills", "sum"),
        hist_close_fills=("close_fills", "sum"),
        hist_flip_fills=("flip_fills", "sum"),
        hist_coins_traded=("coin", "nunique"),
    )
    score_metrics = score_metrics.merge(
        pd.DataFrame(history_totals), on="address", how="left"
    )

    history_coin_stats = pd.DataFrame(
        history_grain.groupby(["address", "coin"], as_index=False).agg(
            coin_net_pnl=("net_pnl", "sum"),
            coin_gross_notional=("gross_notional", "sum"),
        )
    )
    positive_coin_source = pd.DataFrame(
        history_coin_stats.loc[history_coin_stats["coin_net_pnl"] > 0]
    )
    positive_coin_pnl = pd.DataFrame(
        positive_coin_source.groupby("address", as_index=False).agg(
            hist_positive_coin_net_pnl=("coin_net_pnl", "sum")
        )
    )
    idx_top_coin_profit = history_coin_stats.groupby("address")["coin_net_pnl"].idxmax()
    top_coin_profit = history_coin_stats.loc[
        idx_top_coin_profit, ["address", "coin", "coin_net_pnl"]
    ].copy()
    top_coin_profit["score_top_profit_coin"] = top_coin_profit["coin"]
    top_coin_profit["hist_top_coin_net_pnl"] = top_coin_profit["coin_net_pnl"]
    top_coin_profit = top_coin_profit[
        ["address", "score_top_profit_coin", "hist_top_coin_net_pnl"]
    ]
    idx_top_coin_notional = history_coin_stats.groupby("address")[
        "coin_gross_notional"
    ].idxmax()
    top_coin_notional = history_coin_stats.loc[
        idx_top_coin_notional, ["address", "coin_gross_notional"]
    ].copy()
    top_coin_notional["hist_top_coin_gross_notional"] = top_coin_notional[
        "coin_gross_notional"
    ]
    top_coin_notional = top_coin_notional[["address", "hist_top_coin_gross_notional"]]

    idx_top_day_profit = history_daily.groupby("address")["daily_net_pnl"].idxmax()
    top_day_profit = history_daily.loc[
        idx_top_day_profit, ["address", "daily_net_pnl"]
    ].copy()
    top_day_profit["hist_top_day_net_pnl"] = top_day_profit["daily_net_pnl"]
    top_day_profit = top_day_profit[["address", "hist_top_day_net_pnl"]]
    positive_day_source = pd.DataFrame(
        history_daily.loc[history_daily["daily_net_pnl"] > 0]
    )
    positive_day_pnl = pd.DataFrame(
        positive_day_source.groupby("address", as_index=False).agg(
            hist_positive_day_net_pnl=("daily_net_pnl", "sum")
        )
    )
    for frame in [
        positive_coin_pnl,
        top_coin_profit,
        top_coin_notional,
        top_day_profit,
        positive_day_pnl,
    ]:
        score_metrics = score_metrics.merge(frame, on="address", how="left")

    score_metrics["score_net_pnl_per_notional"] = score_metrics[
        "hist_net_pnl"
    ] / score_metrics["hist_gross_notional"].replace(0, pd.NA)
    score_metrics["score_fee_rate_on_notional"] = score_metrics[
        "hist_fees"
    ] / score_metrics["hist_gross_notional"].replace(0, pd.NA)
    score_metrics["score_avg_fill_notional"] = score_metrics[
        "hist_gross_notional"
    ] / score_metrics["hist_fills"].replace(0, pd.NA)
    score_metrics["score_fills"] = score_metrics["hist_fills"]
    score_metrics["score_orders"] = score_metrics["hist_orders"]
    score_metrics["score_net_pnl"] = score_metrics["hist_net_pnl"]
    score_metrics["score_gross_notional"] = score_metrics["hist_gross_notional"]
    score_metrics["score_coins_traded"] = score_metrics["hist_coins_traded"]
    score_metrics["score_realized_win_rate"] = score_metrics[
        "hist_winning_realized_fills"
    ] / (
        score_metrics["hist_winning_realized_fills"]
        + score_metrics["hist_losing_realized_fills"]
    ).replace(0, pd.NA)
    score_metrics["score_profit_factor"] = score_metrics[
        "hist_gross_realized_profit"
    ] / score_metrics["hist_gross_realized_loss"].replace(0, pd.NA)
    score_metrics["score_avg_win"] = score_metrics[
        "hist_gross_realized_profit"
    ] / score_metrics["hist_winning_realized_fills"].replace(0, pd.NA)
    score_metrics["score_avg_loss"] = score_metrics[
        "hist_gross_realized_loss"
    ] / score_metrics["hist_losing_realized_fills"].replace(0, pd.NA)
    score_metrics["score_payoff_ratio"] = score_metrics[
        "score_avg_win"
    ] / score_metrics["score_avg_loss"].replace(0, pd.NA)
    score_metrics["score_crossed_fill_rate"] = score_metrics[
        "hist_crossed_fills"
    ] / score_metrics["hist_fills"].replace(0, pd.NA)
    score_metrics["score_close_fill_rate"] = score_metrics[
        "hist_close_fills"
    ] / score_metrics["hist_fills"].replace(0, pd.NA)
    score_metrics["score_flip_fill_rate"] = score_metrics[
        "hist_flip_fills"
    ] / score_metrics["hist_fills"].replace(0, pd.NA)
    score_metrics["score_coin_profit_concentration"] = score_metrics[
        "hist_top_coin_net_pnl"
    ] / score_metrics["hist_positive_coin_net_pnl"].replace(0, pd.NA)
    score_metrics["score_coin_notional_concentration"] = score_metrics[
        "hist_top_coin_gross_notional"
    ] / score_metrics["hist_gross_notional"].replace(0, pd.NA)
    score_metrics["score_day_profit_concentration"] = score_metrics[
        "hist_top_day_net_pnl"
    ] / score_metrics["hist_positive_day_net_pnl"].replace(0, pd.NA)
    score_metrics["score_best_day_profit_share"] = score_metrics[
        "score_day_profit_concentration"
    ]
    score_metrics["score_max_drawdown_on_notional"] = score_metrics[
        "score_max_drawdown"
    ] / score_metrics["hist_gross_notional"].replace(0, pd.NA)
    score_metrics["score_consistency_adjusted_return"] = (
        score_metrics["score_net_pnl_per_notional"]
        * score_metrics["score_positive_day_rate"]
        / score_metrics["score_max_drawdown_on_notional"].replace(0, pd.NA)
    )

    keep_history_columns = [
        "address",
        "score_sharpe",
        "score_sortino",
        "score_positive_day_rate",
        "score_active_day_rate",
        "score_active_days",
        "score_return_mean",
        "score_return_std",
        "score_return_skew",
        "score_return_kurtosis",
        "score_downside_return_std",
        "score_max_drawdown",
        "score_max_drawdown_on_notional",
        "score_fills",
        "score_orders",
        "score_net_pnl",
        "score_gross_notional",
        "score_coins_traded",
        "score_net_pnl_per_notional",
        "score_fee_rate_on_notional",
        "score_avg_fill_notional",
        "score_realized_win_rate",
        "score_profit_factor",
        "score_avg_win",
        "score_avg_loss",
        "score_payoff_ratio",
        "score_crossed_fill_rate",
        "score_close_fill_rate",
        "score_flip_fill_rate",
        "score_coin_profit_concentration",
        "score_coin_notional_concentration",
        "score_day_profit_concentration",
        "score_best_day_profit_share",
        "score_consistency_adjusted_return",
        "score_top_profit_coin",
    ]
    score_metrics = score_metrics[keep_history_columns]

    forward_day_stats = forward_daily.groupby("address", as_index=False).agg(
        forward_positive_day_rate=("daily_net_pnl", lambda s: (s > 0).mean()),
        forward_active_days=("daily_fills", lambda s: (s > 0).sum()),
        forward_max_drawdown=("drawdown", lambda s: abs(s.min())),
    )
    forward_totals = forward_grain.groupby("address", as_index=False).agg(
        forward_fills=("fills", "sum"),
        forward_orders=("orders", "sum"),
        forward_net_pnl=("net_pnl", "sum"),
        forward_gross_notional=("gross_notional", "sum"),
        forward_coins_traded=("coin", "nunique"),
    )

    forward_metrics = forward_totals.merge(
        pd.DataFrame(forward_day_stats), on="address", how="outer"
    )
    forward_metrics["forward_net_pnl_per_notional"] = forward_metrics[
        "forward_net_pnl"
    ] / forward_metrics["forward_gross_notional"].replace(0, pd.NA)
    forward_metrics["forward_activity_persistence"] = (
        forward_metrics["forward_active_days"] / forward_days
    )

    evaluated = evaluated.merge(score_metrics, on="address", how="left")
    evaluated = evaluated.merge(forward_metrics, on="address", how="left")
    evaluated["forward_return_on_history_notional"] = evaluated[
        "forward_net_pnl"
    ] / evaluated["gross_notional"].replace(0, pd.NA)
    evaluated["forward_market_breadth_persistence"] = evaluated[
        "forward_coins_traded"
    ] / evaluated["coins_traded"].replace(0, pd.NA)
    evaluated["signal_decay_delta"] = (
        evaluated["forward_net_pnl_per_notional"] - evaluated["net_pnl_per_notional"]
    )
    evaluated["signal_decay_ratio"] = evaluated[
        "forward_net_pnl_per_notional"
    ] / evaluated["net_pnl_per_notional"].replace(0, pd.NA)

    score_columns = [
        column for column in evaluated.columns if column.startswith("score_")
    ]
    forward_columns = [
        column
        for column in evaluated.columns
        if column.startswith("forward_") or column.startswith("signal_decay_")
    ]
    for column in score_columns + forward_columns:
        if column != "score_top_profit_coin":
            evaluated[column] = pd.to_numeric(evaluated[column], errors="coerce")
            evaluated[column] = evaluated[column].fillna(0)

    evaluated["evaluation_elapsed_seconds"] = elapsed

    return evaluated[
        ["address"] + score_columns + forward_columns + ["evaluation_elapsed_seconds"]
    ]


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
          AND address IN (SELECT address FROM candidate_addresses)
          AND ({{asset_class:String}} = '' OR asset_class = {{asset_class:String}})
        ORDER BY time, tid
        LIMIT {{limit:UInt32}}
    """

    return query_df(
        sql,
        {
            "history_start_ms": history_start_ms,
            "rebalance_ms": rebalance_ms,
            "asset_class": asset_class or "",
            "limit": limit,
        },
        external_data=_addresses_external_data(address_list),
    )

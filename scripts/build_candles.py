"""
Build Hyperliquid candles from ClickHouse fills

Usage:
  python3 scripts/build_candles.py
  python3 scripts/build_candles.py --overwrite

Options:
  --host localhost
  --port 8123
  --user default
  --password ""
  --database hyperliquid
  --fills-table fills
  --candles-table candles
  --interval 1h
  --asset-class perp
  --workers 4
  --overwrite
"""

import argparse
import multiprocessing as mp
import re
from datetime import datetime, timezone

import clickhouse_connect

DEFAULT_DATABASE = "hyperliquid"
DEFAULT_FILLS_TABLE = "fills"
DEFAULT_CANDLES_TABLE = "candles"
DEFAULT_INTERVAL = "1h"

DAY_MS = 24 * 60 * 60 * 1000

WORKER_CFG = None
WORKER_CLIENT = None


def ident(s: str) -> str:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", s):
        raise ValueError(f"Bad SQL identifier: {s!r}")
    return s


def interval_to_ms(interval: str) -> int:
    m = re.fullmatch(r"(\d+)([smhd])", interval)
    if not m:
        raise ValueError(
            f"Unsupported interval: {interval!r}. "
            "Use fixed UTC-day-aligned intervals like 1m, 5m, 15m, 1h, 4h, or 1d."
        )

    n = int(m.group(1))
    mult = {
        "s": 1_000,
        "m": 60_000,
        "h": 60 * 60_000,
        "d": DAY_MS,
    }[m.group(2)]

    interval_ms = n * mult

    if interval_ms > DAY_MS:
        raise ValueError("Intervals larger than 1d are not supported")

    if DAY_MS % interval_ms != 0:
        raise ValueError(
            f"Interval {interval!r} does not divide evenly into a UTC day. "
            "Use intervals like 1m, 5m, 15m, 30m, 1h, 2h, 4h, 6h, 8h, 12h, or 1d."
        )

    return interval_ms


def now_ms() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)


def floor_ms(ts_ms: int, interval_ms: int) -> int:
    return (ts_ms // interval_ms) * interval_ms


def get_client(cfg):
    return clickhouse_connect.get_client(
        host=cfg["host"],
        port=cfg["port"],
        username=cfg["user"],
        password=cfg["password"],
    )


def create_tables(client, db: str, candles_table: str):
    client.command(f"CREATE DATABASE IF NOT EXISTS {db}")

    client.command(f"""
        CREATE TABLE IF NOT EXISTS {db}.{candles_table}
        (
            bucket_start_ms UInt64,
            bucket_end_ms UInt64,
            coin LowCardinality(String),
            interval LowCardinality(String),
            asset_class LowCardinality(String),
            open Decimal(20, 10),
            high Decimal(20, 10),
            low Decimal(20, 10),
            close Decimal(20, 10),
            volume_base Decimal(20, 10),
            volume_quote Decimal(20, 10),
            updated_at DateTime DEFAULT now()
        )
        ENGINE = MergeTree
        PARTITION BY toYYYYMM(toDateTime(intDiv(bucket_start_ms, 1000), 'UTC'))
        ORDER BY (bucket_start_ms, interval, coin, asset_class)
    """)


def get_tasks(client, cfg):
    db = cfg["database"]
    fills_table = cfg["fills_table"]

    rows = client.query(
        f"""
        SELECT
            coin,
            asset_class,
            min(time) AS first_fill_time_ms
        FROM {db}.{fills_table}
        WHERE time < {{end_ms:UInt64}}
          AND asset_class = {{asset_class:String}}
        GROUP BY
            coin,
            asset_class
        ORDER BY
            coin,
            asset_class
        """,
        parameters={
            "end_ms": cfg["end_exclusive_ms"],
            "asset_class": cfg["asset_class"],
        },
    ).result_rows

    return [
        (str(coin), str(asset_class), int(first_fill_time_ms))
        for coin, asset_class, first_fill_time_ms in rows
    ]


def get_last_candle_ms(coin: str, asset_class: str) -> int | None:
    if WORKER_CLIENT is None or WORKER_CFG is None:
        raise RuntimeError("Worker was not initialized")

    cfg = WORKER_CFG
    db = cfg["database"]
    candles_table = cfg["candles_table"]

    rows = WORKER_CLIENT.query(
        f"""
        SELECT maxOrNull(bucket_start_ms)
        FROM {db}.{candles_table}
        WHERE coin = {{coin:String}}
          AND interval = {{interval:String}}
          AND asset_class = {{asset_class:String}}
        """,
        parameters={
            "coin": coin,
            "interval": cfg["interval"],
            "asset_class": asset_class,
        },
    ).result_rows

    value = rows[0][0]
    return None if value is None else int(value)


def insert_candles(
    coin: str,
    asset_class: str,
    start_ms: int,
    end_exclusive_ms: int,
):
    if WORKER_CLIENT is None or WORKER_CFG is None:
        raise RuntimeError("Worker was not initialized")

    cfg = WORKER_CFG
    db = cfg["database"]
    fills_table = cfg["fills_table"]
    candles_table = cfg["candles_table"]

    WORKER_CLIENT.command(
        f"""
        INSERT INTO {db}.{candles_table}
        (
            bucket_start_ms,
            bucket_end_ms,
            coin,
            interval,
            asset_class,
            open,
            high,
            low,
            close,
            volume_base,
            volume_quote
        )
        WITH {{interval_ms:UInt64}} AS interval_ms
        SELECT
            bucket_start_ms,
            bucket_start_ms + interval_ms AS bucket_end_ms,
            coin,
            {{interval:String}} AS interval,
            asset_class,
            argMin(px, tuple(time, tid)) AS open,
            max(px) AS high,
            min(px) AS low,
            argMax(px, tuple(time, tid)) AS close,
            sum(sz) AS volume_base,
            sum(px * sz) AS volume_quote
        FROM
        (
            SELECT
                toUInt64(intDiv(time, interval_ms) * interval_ms) AS bucket_start_ms,
                coin,
                asset_class,
                px,
                sz,
                time,
                tid
            FROM {db}.{fills_table}
            WHERE coin = {{coin:String}}
              AND asset_class = {{asset_class:String}}
              AND time >= {{start_ms:UInt64}}
              AND time < {{end_ms:UInt64}}
        )
        GROUP BY
            bucket_start_ms,
            coin,
            asset_class
        ORDER BY
            bucket_start_ms,
            coin,
            asset_class
        """,
        parameters={
            "interval": cfg["interval"],
            "interval_ms": cfg["interval_ms"],
            "coin": coin,
            "asset_class": asset_class,
            "start_ms": start_ms,
            "end_ms": end_exclusive_ms,
        },
    )


def init_worker(cfg):
    global WORKER_CFG, WORKER_CLIENT
    WORKER_CFG = cfg
    WORKER_CLIENT = get_client(cfg)


def update_one(task):
    coin, asset_class, first_fill_time_ms = task

    try:
        if WORKER_CFG is None:
            raise RuntimeError("Worker was not initialized")

        cfg = WORKER_CFG
        first_bucket_ms = floor_ms(first_fill_time_ms, cfg["interval_ms"])
        last_candle_ms = get_last_candle_ms(coin, asset_class)

        if last_candle_ms is None:
            start_ms = first_bucket_ms
        else:
            start_ms = last_candle_ms + cfg["interval_ms"]

        end_exclusive_ms = cfg["end_exclusive_ms"]

        if start_ms >= end_exclusive_ms:
            return {
                "ok": True,
                "coin": coin,
                "asset_class": asset_class,
                "skipped": True,
            }

        insert_candles(coin, asset_class, start_ms, end_exclusive_ms)

        return {
            "ok": True,
            "coin": coin,
            "asset_class": asset_class,
            "start_ms": start_ms,
            "end_ms": end_exclusive_ms,
            "skipped": False,
        }

    except Exception as e:
        return {
            "ok": False,
            "coin": coin,
            "asset_class": asset_class,
            "error": repr(e),
        }


def print_result(res) -> int:
    if not res["ok"]:
        print(f"ERROR: {res['coin']} {res['asset_class']}: {res['error']}")
        return 1

    if res["skipped"]:
        print(f"skipped: {res['coin']} {res['asset_class']} already_current=true")
        return 0

    print(
        f"updated: {res['coin']} {res['asset_class']} "
        f"start_ms={res['start_ms']} "
        f"end_ms={res['end_ms']}"
    )
    return 0


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--host", default="localhost")
    p.add_argument("--port", type=int, default=8123)
    p.add_argument("--user", default="default")
    p.add_argument("--password", default="")
    p.add_argument("--database", default=DEFAULT_DATABASE)
    p.add_argument("--fills-table", default=DEFAULT_FILLS_TABLE)
    p.add_argument("--candles-table", default=DEFAULT_CANDLES_TABLE)
    p.add_argument("--interval", default=DEFAULT_INTERVAL)
    p.add_argument("--asset-class", default="perp", choices=("perp", "spot"))
    p.add_argument("--workers", type=int, default=4)
    p.add_argument(
        "--overwrite",
        action="store_true",
        help="Drop and recreate the candles table before building candles.",
    )

    args = p.parse_args()

    db = ident(args.database)
    fills_table = ident(args.fills_table)
    candles_table = ident(args.candles_table)
    interval_ms = interval_to_ms(args.interval)

    # Exclude the current in-progress candle.
    end_exclusive_ms = floor_ms(now_ms(), interval_ms)

    cfg = {
        "host": args.host,
        "port": args.port,
        "user": args.user,
        "password": args.password,
        "database": db,
        "fills_table": fills_table,
        "candles_table": candles_table,
        "interval": args.interval,
        "interval_ms": interval_ms,
        "asset_class": args.asset_class,
        "end_exclusive_ms": end_exclusive_ms,
    }

    client = get_client(cfg)

    if args.overwrite:
        client.command(f"CREATE DATABASE IF NOT EXISTS {db}")
        client.command(f"DROP TABLE IF EXISTS {db}.{candles_table}")

    create_tables(client, db, candles_table)

    tasks = get_tasks(client, cfg)

    if not tasks:
        print("done")
        return

    print(
        f"tasks={len(tasks)} interval={args.interval} "
        f"asset_class={args.asset_class} "
        f"end_exclusive_ms={end_exclusive_ms} overwrite={args.overwrite}"
    )

    errors = 0

    if args.workers == 1:
        init_worker(cfg)
        for task in tasks:
            errors += print_result(update_one(task))
    else:
        with mp.Pool(
            processes=args.workers,
            initializer=init_worker,
            initargs=(cfg,),
        ) as pool:
            for res in pool.imap_unordered(update_one, tasks):
                errors += print_result(res)

    if errors:
        raise SystemExit(1)

    print("done")


if __name__ == "__main__":
    import time

    started_at = time.perf_counter()
    main()
    print(time.perf_counter() - started_at)

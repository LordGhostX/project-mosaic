"""
Usage:
  python3 scripts/ingest_lz4.py data/node_fills
  python3 scripts/ingest_lz4.py data/node_fills_by_block

Options:
  --host localhost --port 8123 --user default --password ""
  --database hyperliquid --table fills --data-root data
  --workers 4
"""

import argparse
import json
import multiprocessing as mp
import re
from decimal import Decimal
from pathlib import Path

import clickhouse_connect
import lz4.frame

DEFAULT_DATABASE = "hyperliquid"
DEFAULT_FILLS_TABLE = "fills"
TRACKING_TABLE = "ingested_lz4_files"

IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

WORKER_CFG = None
WORKER_CLIENT = None

FILL_COLUMNS = [
    "user_address",
    "coin",
    "px",
    "sz",
    "side",
    "time",
    "start_position",
    "dir",
    "closed_pnl",
    "hash",
    "oid",
    "crossed",
    "fee",
    "tid",
    "fee_token",
]


def ident(s: str) -> str:
    if not IDENT.match(s):
        raise ValueError(f"Bad SQL identifier: {s}")
    return s


def mode_for(path: Path) -> str:
    if path.name in {"node_fills", "node_fills_by_block"}:
        return path.name
    raise ValueError("Input dir must be named node_fills or node_fills_by_block")


def dec(v) -> Decimal:
    if v in (None, ""):
        return Decimal("0")
    return Decimal(str(v))


def u64(v) -> int:
    if v in (None, ""):
        return 0
    return int(v)


def fill_row(event):
    user_address, fill = event[0], event[1]

    return (
        str(user_address),
        str(fill.get("coin", "")),
        dec(fill.get("px")),
        dec(fill.get("sz")),
        str(fill.get("side", "")),
        u64(fill.get("time")),
        dec(fill.get("startPosition")),
        str(fill.get("dir", "")),
        dec(fill.get("closedPnl")),
        str(fill.get("hash", "")),
        u64(fill.get("oid")),
        bool(fill.get("crossed", False)),
        dec(fill.get("fee")),
        u64(fill.get("tid")),
        str(fill.get("feeToken", "")),
    )


def read_rows(path: Path, mode: str):
    rows = []

    with lz4.frame.open(path, mode="rt", encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue

            raw = json.loads(line)

            if mode == "node_fills":
                rows.append(fill_row(raw))
            else:
                rows.extend(fill_row(event) for event in raw.get("events", []))

    return rows


def get_client(cfg):
    return clickhouse_connect.get_client(
        host=cfg["host"],
        port=cfg["port"],
        username=cfg["user"],
        password=cfg["password"],
    )


def create_tables(client, db: str, fills_table: str):
    client.command(f"CREATE DATABASE IF NOT EXISTS {db}")

    client.command(f"""
        CREATE TABLE IF NOT EXISTS {db}.{fills_table}
        (
            user_address String,
            coin String,
            px Decimal(20, 10),
            sz Decimal(20, 10),
            side String,
            time UInt64,
            start_position Decimal(20, 10),
            dir String,
            closed_pnl Decimal(20, 10),
            hash String,
            oid UInt64,
            crossed Bool,
            fee Decimal(20, 10),
            tid UInt64,
            fee_token String
        )
        ENGINE = MergeTree
        PARTITION BY toYYYYMM(toDateTime(intDiv(time, 1000), 'UTC'))
        ORDER BY (coin, time, user_address, tid)
    """)

    client.command(f"""
        CREATE TABLE IF NOT EXISTS {db}.{TRACKING_TABLE}
        (
            file_key String,
            ingested_at DateTime DEFAULT now()
        )
        ENGINE = MergeTree
        ORDER BY file_key
    """)


def init_worker(cfg):
    global WORKER_CFG, WORKER_CLIENT
    WORKER_CFG = cfg
    WORKER_CLIENT = get_client(cfg)


def ingest_one(task):
    if WORKER_CLIENT is None or WORKER_CFG is None:
        raise RuntimeError("Worker was not initialized")

    path_str, file_key, mode = task
    rows = read_rows(Path(path_str), mode)

    if rows:
        WORKER_CLIENT.insert(
            WORKER_CFG["table"],
            rows,
            database=WORKER_CFG["database"],
            column_names=FILL_COLUMNS,
        )

    WORKER_CLIENT.insert(
        TRACKING_TABLE,
        [(file_key,)],
        database=WORKER_CFG["database"],
        column_names=["file_key"],
    )

    return file_key, len(rows)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("input_dir")
    p.add_argument("--data-root", default="data")
    p.add_argument("--host", default="localhost")
    p.add_argument("--port", type=int, default=8123)
    p.add_argument("--user", default="default")
    p.add_argument("--password", default="")
    p.add_argument("--database", default=DEFAULT_DATABASE)
    p.add_argument("--table", default=DEFAULT_FILLS_TABLE)
    p.add_argument("--workers", type=int, default=4)
    args = p.parse_args()

    db = ident(args.database)
    table = ident(args.table)

    input_dir = Path(args.input_dir).resolve()
    data_root = Path(args.data_root).resolve()
    mode = mode_for(input_dir)

    cfg = {
        "host": args.host,
        "port": args.port,
        "user": args.user,
        "password": args.password,
        "database": db,
        "table": table,
    }

    client = get_client(cfg)
    create_tables(client, db, table)

    seen = {
        row[0]
        for row in client.query(
            f"SELECT file_key FROM {db}.{TRACKING_TABLE}"
        ).result_rows
    }

    tasks = [
        (str(path), path.relative_to(data_root).as_posix(), mode)
        for path in sorted(input_dir.rglob("*.lz4"))
        if path.relative_to(data_root).as_posix() not in seen
    ]

    if not tasks:
        print("done")
        return

    workers = max(1, args.workers)

    if workers == 1:
        init_worker(cfg)
        for task in tasks:
            file_key, rows = ingest_one(task)
            print(f"ingested: {file_key} rows={rows}")
    else:
        with mp.Pool(
            processes=workers, initializer=init_worker, initargs=(cfg,)
        ) as pool:
            for file_key, rows in pool.imap_unordered(ingest_one, tasks):
                print(f"ingested: {file_key} rows={rows}")

    print("done")


if __name__ == "__main__":
    main()

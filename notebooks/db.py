import os
from functools import lru_cache

import clickhouse_connect

CLICKHOUSE_HOST = os.getenv("CLICKHOUSE_HOST", "localhost")
CLICKHOUSE_PORT = int(os.getenv("CLICKHOUSE_PORT", "8123"))
CLICKHOUSE_USER = os.getenv("CLICKHOUSE_USER", "default")
CLICKHOUSE_PASSWORD = os.getenv("CLICKHOUSE_PASSWORD", "")
CLICKHOUSE_DATABASE = os.getenv("CLICKHOUSE_DATABASE", "hyperliquid")
FILLS_TABLE = os.getenv("CLICKHOUSE_FILLS_TABLE", "fills")


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


def get_fills_row_count() -> int:
    result = get_client().query(f"SELECT count() FROM {FILLS_TABLE}")
    return int(result.result_rows[0][0])

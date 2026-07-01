import bisect
import math
from datetime import datetime, timezone

import pandas as pd


def timestamp(value) -> pd.Timestamp:
    ts = pd.Timestamp(value)
    if pd.isna(ts) or not isinstance(ts, pd.Timestamp):
        raise ValueError("timestamp value cannot be NaT")
    if ts.tzinfo is None:
        return ts.tz_localize("UTC")
    return ts.tz_convert("UTC")


def python_datetime(value) -> datetime:
    ts = timestamp(value)
    return datetime.fromtimestamp(ts.value / 1_000_000_000, tz=timezone.utc)


def hour_ms(value) -> int:
    return int(timestamp(value).floor("h").value // 1_000_000)


def numeric_value(row, column, default=0.0) -> float:
    value = row.get(column, default)
    if pd.isna(value):
        return default
    return float(value)


def fill_segments(row) -> list[tuple[str, str, float]]:
    direction = str(row.get("dir", ""))
    direction_lower = direction.lower()
    px = numeric_value(row, "px")
    sz = numeric_value(row, "sz")
    start_position = numeric_value(row, "start_position")
    fill_notional = px * sz

    if px <= 0 or sz <= 0:
        return []

    if direction == "Long > Short":
        close_sz = min(abs(start_position), sz)
        open_sz = max(sz - close_sz, 0)
        segments = []
        if close_sz > 0:
            segments.append(("close", "long", close_sz * px))
        if open_sz > 0:
            segments.append(("open", "short", open_sz * px))
        return segments

    if direction == "Short > Long":
        close_sz = min(abs(start_position), sz)
        open_sz = max(sz - close_sz, 0)
        segments = []
        if close_sz > 0:
            segments.append(("close", "short", close_sz * px))
        if open_sz > 0:
            segments.append(("open", "long", open_sz * px))
        return segments

    if "open" in direction_lower and "long" in direction_lower:
        return [("open", "long", fill_notional)]
    if "open" in direction_lower and "short" in direction_lower:
        return [("open", "short", fill_notional)]
    if "close" in direction_lower and "long" in direction_lower:
        return [("close", "long", fill_notional)]
    if "close" in direction_lower and "short" in direction_lower:
        return [("close", "short", fill_notional)]

    return []


def add_fill_buckets(fills: pd.DataFrame) -> pd.DataFrame:
    if fills.empty:
        return fills.copy()

    out = fills.copy()
    out["fill_at"] = pd.to_datetime(out["fill_at"], utc=True)
    out["hour"] = out["fill_at"].dt.floor("h")
    out["hour_ms"] = out["hour"].map(lambda value: hour_ms(value)).astype("int64")
    numeric_columns = ["px", "sz", "start_position", "closed_pnl", "fee", "net_pnl"]
    for column in numeric_columns:
        if column in out.columns:
            out[column] = pd.Series(
                pd.to_numeric(out[column], errors="coerce"), index=out.index
            ).fillna(0)
    return out


def history_open_notional_by_address(history_fills: pd.DataFrame) -> dict[str, float]:
    totals = {}
    if history_fills.empty:
        return totals

    for _, row in history_fills.iterrows():
        address = row["address"]
        for action, _, notional in fill_segments(row):
            if action == "open":
                totals[address] = totals.get(address, 0.0) + notional
    return totals


def build_price_lookup(candles: pd.DataFrame) -> dict[tuple[str, int], float]:
    lookup = {}
    if candles.empty:
        return lookup

    prices = candles.copy()
    for column in ["bucket_start_ms", "close"]:
        prices[column] = pd.to_numeric(prices[column], errors="coerce")

    valid_prices = prices.dropna(subset=["bucket_start_ms", "close"])
    for index in valid_prices.index:
        coin = str(valid_prices.at[index, "coin"])
        bucket_start_ms = int(valid_prices.at[index, "bucket_start_ms"])
        close = float(valid_prices.at[index, "close"])
        lookup[(coin, bucket_start_ms)] = close
    return lookup


def build_price_history(candles: pd.DataFrame) -> dict[str, list[tuple[int, float]]]:
    history: dict[str, list[tuple[int, float]]] = {}
    if candles.empty:
        return history

    prices = candles.copy()
    for column in ["bucket_start_ms", "close"]:
        prices[column] = pd.to_numeric(prices[column], errors="coerce")

    valid_prices = prices.dropna(subset=["bucket_start_ms", "close"]).sort_values(
        ["coin", "bucket_start_ms"]
    )
    for index in valid_prices.index:
        coin = str(valid_prices.at[index, "coin"])
        bucket_start_ms = int(valid_prices.at[index, "bucket_start_ms"])
        close = float(valid_prices.at[index, "close"])
        if close <= 0:
            continue
        history.setdefault(coin, []).append((bucket_start_ms, close))
    return history


def lookup_price(
    price_lookup: dict[tuple[str, int], float],
    coin: str,
    hour_ms_value: int,
) -> float | None:
    price = price_lookup.get((coin, int(hour_ms_value)))
    if price is None or price <= 0:
        return None
    return price


def lookup_price_with_fallback(
    price_lookup: dict[tuple[str, int], float],
    price_history: dict[str, list[tuple[int, float]]],
    coin: str,
    hour_ms_value: int,
) -> tuple[float | None, bool, int | None]:
    requested_hour_ms = int(hour_ms_value)
    exact_price = lookup_price(price_lookup, coin, requested_hour_ms)
    if exact_price is not None:
        return exact_price, False, requested_hour_ms

    points = price_history.get(coin, [])
    if not points:
        return None, False, None

    hours = [hour_ms for hour_ms, _ in points]
    index = bisect.bisect_right(hours, requested_hour_ms)
    if index > 0:
        fallback_hour_ms, fallback_price = points[index - 1]
        return fallback_price, True, fallback_hour_ms

    fallback_hour_ms, fallback_price = points[0]
    return fallback_price, True, fallback_hour_ms


def series_sum(df: pd.DataFrame, column: str) -> float:
    return float(pd.Series(pd.to_numeric(df[column], errors="coerce")).fillna(0).sum())


def series_mean(df: pd.DataFrame, column: str) -> float:
    return float(pd.Series(pd.to_numeric(df[column], errors="coerce")).fillna(0).mean())


def series_max(df: pd.DataFrame, column: str) -> float:
    return float(pd.Series(pd.to_numeric(df[column], errors="coerce")).fillna(0).max())


def series_min(df: pd.DataFrame, column: str) -> float:
    return float(pd.Series(pd.to_numeric(df[column], errors="coerce")).fillna(0).min())


def series_last(df: pd.DataFrame, column: str) -> float:
    series = pd.Series(pd.to_numeric(df[column], errors="coerce")).fillna(0)
    return float(series.iloc[-1])


def float_list(values) -> list[float]:
    floats = []
    for value in values:
        if pd.isna(value):
            continue
        float_value = float(value)
        if math.isfinite(float_value):
            floats.append(float_value)
    return floats


def float_mean(values) -> float:
    floats = float_list(values)
    return sum(floats) / len(floats) if floats else 0.0


def float_sample_std(values) -> float:
    floats = float_list(values)
    if len(floats) < 2:
        return 0.0

    mean = sum(floats) / len(floats)
    variance = sum((value - mean) ** 2 for value in floats) / (len(floats) - 1)
    return variance**0.5


def float_skew(values) -> float:
    floats = float_list(values)
    if len(floats) < 3:
        return 0.0

    mean = sum(floats) / len(floats)
    variance = sum((value - mean) ** 2 for value in floats) / len(floats)
    std = variance**0.5
    if std == 0:
        return 0.0

    return sum(((value - mean) / std) ** 3 for value in floats) / len(floats)


def float_excess_kurtosis(values) -> float:
    floats = float_list(values)
    if len(floats) < 4:
        return 0.0

    mean = sum(floats) / len(floats)
    variance = sum((value - mean) ** 2 for value in floats) / len(floats)
    std = variance**0.5
    if std == 0:
        return 0.0

    return sum(((value - mean) / std) ** 4 for value in floats) / len(floats) - 3

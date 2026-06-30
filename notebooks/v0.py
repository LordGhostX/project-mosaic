import time

import pandas as pd
import queries as q

DEFAULT_CONFIG = {
    "rebalance_at": "2025-07-01",
    "active_days": 7,
    "history_days": 30,
    "min_history_fills": 1,
    "min_net_pnl": 0,
    "asset_class": "perp",
    "candidate_limit": 0,
    "forward_days": 7,
    "exclude_base_columns": True,
}


DEFAULT_FILTERS = {
    "min_active_history_days": 14,
    "min_net_pnl": 0,
    "min_net_pnl_per_notional": 0.001,
    "min_fee_rate_on_notional": 0,
    "min_avg_seconds_between_fills": 600,
    "min_close_fill_rate": 0.25,
    "min_avg_fill_notional": 250,
    "min_crossed_fill_rate": 0,
    "max_history_fills_per_day": 50,
    "max_orders_per_day": 25,
    "max_flip_fill_rate": 0.25,
}


def load(**overrides):
    config = DEFAULT_CONFIG | overrides

    candidates = q.get_profitable_active_traders(
        config["rebalance_at"],
        active_days=config["active_days"],
        history_days=config["history_days"],
        min_history_fills=config["min_history_fills"],
        min_net_pnl=config["min_net_pnl"],
        asset_class=config["asset_class"],
        limit=config["candidate_limit"],
    )

    return {
        "config": config,
        "fills_bounds": q.get_fills_time_bounds(),
        "candidates": candidates,
    }


def filter_candidates(candidates, **overrides):
    filters = DEFAULT_FILTERS | overrides
    filtered = candidates.copy()

    min_filters = {
        "active_history_days": filters["min_active_history_days"],
        "net_pnl": filters["min_net_pnl"],
        "net_pnl_per_notional": filters["min_net_pnl_per_notional"],
        "fee_rate_on_notional": filters["min_fee_rate_on_notional"],
        "avg_seconds_between_fills": filters["min_avg_seconds_between_fills"],
        "close_fill_rate": filters["min_close_fill_rate"],
        "avg_fill_notional": filters["min_avg_fill_notional"],
        "crossed_fill_rate": filters["min_crossed_fill_rate"],
    }
    max_filters = {
        "history_fills_per_day": filters["max_history_fills_per_day"],
        "orders_per_day": filters["max_orders_per_day"],
        "flip_fill_rate": filters["max_flip_fill_rate"],
    }

    for column, value in min_filters.items():
        if value is not None:
            filtered = filtered[filtered[column] >= value]

    for column, value in max_filters.items():
        if value not in (None, 0):
            filtered = filtered[filtered[column] <= value]

    return filtered.sort_values("net_pnl", ascending=False).reset_index(drop=True)


def evaluate_candidates(candidates, **overrides):
    config = DEFAULT_CONFIG | overrides

    return q.evaluate_candidates(
        candidates,
        config["rebalance_at"],
        history_days=config["history_days"],
        forward_days=config["forward_days"],
        asset_class=config["asset_class"],
        exclude_base_columns=config["exclude_base_columns"],
    )


def generate_all_evaluated():
    start_date = pd.Timestamp("2025-07-01")
    end_date = pd.Timestamp(q.get_fills_time_bounds()["last_fill_at"])

    all_evaluated = []

    rebalance_at = start_date

    while rebalance_at <= end_date:
        rebalance_str = rebalance_at.strftime("%Y-%m-%d")

        r = load(rebalance_at=rebalance_str)
        raw = r["candidates"]

        filtered = filter_candidates(raw)
        evaluated = evaluate_candidates(filtered, rebalance_at=rebalance_str)

        evaluated["rebalance_at"] = rebalance_str

        all_evaluated.append(evaluated)

        rebalance_at += pd.Timedelta(days=7)

    return pd.concat(all_evaluated, ignore_index=True)


if __name__ == "__main__":
    started_at = time.perf_counter()

    all_evaluated = generate_all_evaluated()
    all_evaluated.to_csv("../data/all_evaluated.csv", index=False)

    print("Elapsed Seconds:", round(time.perf_counter() - started_at, 3))

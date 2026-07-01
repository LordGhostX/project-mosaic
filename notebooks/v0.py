import time

import matplotlib.pyplot as plt
import pandas as pd
import queries as q

DEFAULT_CONFIG = {
    "rebalance_at": "2025-07-01",
    "active_days": 7,
    "history_days": 30,
    "min_history_fills": 1,
    "min_net_pnl": 0,
    "asset_class": "perp",
    "forward_days": 7,
    "exclude_base_columns": True,
}


DEFAULT_FILTERS = {
    "min_active_history_days": 14,
    "min_net_pnl": 0,
    "min_net_pnl_per_notional": 0.001,
    "min_fee_rate_on_notional": 0,
    "min_realized_pnl_fills": 5,
    "min_realized_win_rate": 0.45,
    "min_avg_seconds_between_fills": 600,
    "min_close_fill_rate": 0.25,
    "min_avg_fill_notional": 250,
    "min_crossed_fill_rate": 0,
    "max_fee_rate_on_notional": 0.0015,
    "max_crossed_fill_rate": 0.85,
    "max_history_fills_per_day": 40,
    "max_orders_per_day": 20,
    "max_flip_fill_rate": 0.20,
}


DEFAULT_SCORING = {
    "group_column": "rebalance_at",
    "guardrail_quantile": 0.90,
    "guardrail_penalty": 1.0,
    "apply_guardrail_penalty": True,
    "min_score_gross_notional": 0,
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
        "realized_pnl_fills": filters["min_realized_pnl_fills"],
        "realized_win_rate": filters["min_realized_win_rate"],
        "avg_seconds_between_fills": filters["min_avg_seconds_between_fills"],
        "close_fill_rate": filters["min_close_fill_rate"],
        "avg_fill_notional": filters["min_avg_fill_notional"],
        "crossed_fill_rate": filters["min_crossed_fill_rate"],
    }
    max_filters = {
        "fee_rate_on_notional": filters["max_fee_rate_on_notional"],
        "crossed_fill_rate": filters["max_crossed_fill_rate"],
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


def score_candidates(evaluated, **overrides):
    config = DEFAULT_SCORING | overrides
    scored = evaluated.copy()

    positive_weights = {
        "score_realized_win_rate": 0.30,
        "score_return_mean": 0.20,
        "score_positive_day_rate": 0.05,
    }
    consistency_features = [
        "score_sharpe",
        "score_consistency_adjusted_return",
    ]
    penalty_weights = {
        "score_max_drawdown_on_notional": 0.15,
        "score_fee_rate_on_notional": 0.10,
        "score_crossed_fill_rate": 0.05,
        "score_day_profit_concentration": 0.05,
    }
    guardrail_features = [
        "score_max_drawdown_on_notional",
        "score_downside_return_std",
        "score_fee_rate_on_notional",
        "score_crossed_fill_rate",
        "score_day_profit_concentration",
    ]
    required_columns = (
        list(positive_weights)
        + consistency_features
        + list(penalty_weights)
        + guardrail_features
        + ["score_gross_notional"]
    )
    missing = sorted({column for column in required_columns if column not in scored})
    if missing:
        raise ValueError(f"evaluated is missing required scoring columns: {missing}")

    group_column = config["group_column"]
    if group_column not in scored.columns:
        group_column = None

    def numeric_series(column):
        series = pd.Series(
            pd.to_numeric(scored[column], errors="coerce"),
            index=scored.index,
        )
        series = series.mask(series == float("inf"), pd.NA)
        series = series.mask(series == float("-inf"), pd.NA)
        return series.fillna(0)

    def percentile_rank(column):
        series = numeric_series(column)
        if group_column is None:
            return series.rank(pct=True, method="average")

        return series.groupby(scored[group_column]).rank(pct=True, method="average")

    quality = pd.Series(0.0, index=scored.index)
    for column, weight in positive_weights.items():
        quality = quality + percentile_rank(column) * weight

    consistency_rank = sum(
        percentile_rank(column) for column in consistency_features
    ) / len(consistency_features)
    quality = quality + consistency_rank * 0.25

    risk_penalty = pd.Series(0.0, index=scored.index)
    for column, weight in penalty_weights.items():
        risk_penalty = risk_penalty + percentile_rank(column) * weight

    scored["candidate_quality_score"] = quality
    scored["candidate_risk_penalty"] = risk_penalty
    scored["candidate_score"] = quality - risk_penalty

    guardrail_quantile = config["guardrail_quantile"]
    failed_guardrail_count = pd.Series(0, index=scored.index)
    for column in guardrail_features:
        series = numeric_series(column)
        if group_column is None:
            threshold = pd.Series(
                series.quantile(guardrail_quantile), index=scored.index
            )
        else:
            threshold = series.groupby(scored[group_column]).transform(
                lambda values: values.quantile(guardrail_quantile)
            )
        guardrail_column = f"fails_guardrail_{column.removeprefix('score_')}"
        scored[guardrail_column] = series >= threshold
        failed_guardrail_count = failed_guardrail_count + scored[
            guardrail_column
        ].astype(int)

    scored["failed_guardrail_count"] = failed_guardrail_count
    scored["passes_score_guardrails"] = scored["failed_guardrail_count"] == 0
    scored["passes_liquidity_floor"] = (
        numeric_series("score_gross_notional") >= config["min_score_gross_notional"]
    )
    scored["eligible_for_ranking"] = (
        scored["passes_score_guardrails"] & scored["passes_liquidity_floor"]
    )

    guardrail_penalty = config["guardrail_penalty"]
    if config["apply_guardrail_penalty"]:
        scored["candidate_score_guarded"] = scored["candidate_score"] - (
            scored["failed_guardrail_count"] * guardrail_penalty
        )
    else:
        scored["candidate_score_guarded"] = scored["candidate_score"]

    sort_columns = [
        "eligible_for_ranking",
        "candidate_score_guarded",
        "candidate_score",
    ]
    ascending = [False, False, False]
    if group_column is not None:
        sort_columns = [group_column] + sort_columns
        ascending = [True] + ascending

    scored = scored.sort_values(sort_columns, ascending=ascending).reset_index(
        drop=True
    )
    if group_column is None:
        scored["candidate_rank"] = scored.index + 1
    else:
        scored["candidate_rank"] = scored.groupby(group_column).cumcount() + 1

    score_columns = [
        "candidate_rank",
        "candidate_score_guarded",
        "candidate_score",
        "candidate_quality_score",
        "candidate_risk_penalty",
        "eligible_for_ranking",
        "passes_score_guardrails",
        "passes_liquidity_floor",
        "failed_guardrail_count",
    ]
    guardrail_columns = [
        column for column in scored.columns if column.startswith("fails_guardrail_")
    ]
    other_columns = [
        column
        for column in scored.columns
        if column not in score_columns and column not in guardrail_columns
    ]

    return scored[score_columns + guardrail_columns + other_columns]


def generate_all_evaluated(
    start_date="2025-07-01", end_date=None, days=7, forward_days=7
):
    started_at = time.perf_counter()

    if end_date is None:
        end_date = q.get_fills_time_bounds()["last_fill_at"]

    start_date = pd.Timestamp(start_date)
    end_date = pd.Timestamp(end_date)

    all_evaluated = []

    rebalance_at = start_date

    while rebalance_at <= end_date:
        rebalance_str = rebalance_at.strftime("%Y-%m-%d")

        r = load(rebalance_at=rebalance_str)
        raw = r["candidates"]

        filtered = filter_candidates(raw)
        evaluated = evaluate_candidates(
            filtered, rebalance_at=rebalance_str, forward_days=forward_days
        )

        evaluated["rebalance_at"] = rebalance_str

        all_evaluated.append(evaluated)

        rebalance_at += pd.Timedelta(days=days)

    all_evaluated_df = (
        pd.concat(all_evaluated, ignore_index=True)
        .sort_values("rebalance_at")
        .reset_index(drop=True)
    )
    all_evaluated_df["evaluation_elapsed_seconds"] = round(
        time.perf_counter() - started_at, 3
    )
    return all_evaluated_df


if __name__ == "__main__":
    all_evaluated = generate_all_evaluated()
    all_evaluated.to_csv("../reports/all_evaluated.csv", index=False)
    print("Elapsed Seconds:", all_evaluated.iloc[0]["evaluation_elapsed_seconds"])

    all_scored = score_candidates(all_evaluated)
    all_scored.groupby("rebalance_at")[
        "forward_return_on_history_notional"
    ].mean().cumsum().plot(label="Base")
    all_scored.groupby("rebalance_at").head(50).groupby("rebalance_at")[
        "forward_return_on_history_notional"
    ].mean().cumsum().plot(label="Top 50")
    all_scored.groupby("rebalance_at").head(75).groupby("rebalance_at")[
        "forward_return_on_history_notional"
    ].mean().cumsum().plot(label="Top 75")
    all_scored.groupby("rebalance_at").head(100).groupby("rebalance_at")[
        "forward_return_on_history_notional"
    ].mean().cumsum().plot(label="Top 100")
    plt.legend()
    plt.show()

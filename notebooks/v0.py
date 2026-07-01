import time

import matplotlib.pyplot as plt
import pandas as pd
import queries as q
import utils as u

DEFAULT_CONFIG = {
    "rebalance_at": "2025-07-01",
    "active_days": 7,
    "history_days": 30,
    "min_history_fills": 1,
    "min_net_pnl": 0,
    "asset_class": "perp",
    "forward_days": 7,
    "exclude_forward_metrics": False,
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


def _numeric_series(df, column):
    series = pd.Series(
        pd.to_numeric(df[column], errors="coerce"),
        index=df.index,
    )
    series = series.mask(series == float("inf"), pd.NA)
    series = series.mask(series == float("-inf"), pd.NA)
    return series.fillna(0)


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
        exclude_forward_metrics=config["exclude_forward_metrics"],
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

    def percentile_rank(column):
        series = _numeric_series(scored, column)
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
        series = _numeric_series(scored, column)
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
        _numeric_series(scored, "score_gross_notional")
        >= config["min_score_gross_notional"]
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
    start_date="2025-07-01",
    end_date=None,
    days=7,
    exclude_forward_metrics=False,
    **overrides,
):
    started_at = time.perf_counter()

    if end_date is None:
        end_date = q.get_fills_time_bounds()["last_fill_at"]

    start_date = pd.Timestamp(start_date)
    end_date = pd.Timestamp(end_date)

    all_evaluated = []

    rebalance_at = start_date

    while rebalance_at <= end_date:
        if (
            not exclude_forward_metrics
            and rebalance_at + pd.Timedelta(days=days) > end_date
        ):
            break

        rebalance_str = rebalance_at.strftime("%Y-%m-%d")

        r = load(rebalance_at=rebalance_str, **overrides)
        raw = r["candidates"]

        filtered = filter_candidates(raw, **overrides)
        evaluated = evaluate_candidates(
            filtered,
            rebalance_at=rebalance_str,
            forward_days=days,
            exclude_forward_metrics=exclude_forward_metrics,
            **overrides,
        )

        evaluated["rebalance_at"] = rebalance_str

        all_evaluated.append(evaluated)

        rebalance_at += pd.Timedelta(days=days)

    if not all_evaluated:
        return pd.DataFrame()

    all_evaluated_df = (
        pd.concat(all_evaluated, ignore_index=True)
        .sort_values("rebalance_at")
        .reset_index(drop=True)
    )
    all_evaluated_df["evaluation_elapsed_seconds"] = round(
        time.perf_counter() - started_at, 3
    )
    return all_evaluated_df


def _hourly_trade_intents(fills, session_end):
    if fills.empty:
        return pd.DataFrame()

    rows = []
    for _, fill in fills.iterrows():
        source_hour = u.timestamp(fill["hour"])
        execution_hour = source_hour + pd.Timedelta(hours=1)
        if execution_hour >= session_end:
            continue

        for action, side, notional in u.fill_segments(fill):
            if notional <= 0:
                continue
            rows.append(
                {
                    "source_hour": source_hour,
                    "execution_hour": execution_hour,
                    "source_hour_ms": u.hour_ms(source_hour),
                    "address": fill["address"],
                    "coin": fill["coin"],
                    "side": side,
                    "action": action,
                    "trader_notional": notional,
                    "source_oid": fill["oid"],
                    "source_fills": 1,
                }
            )

    if not rows:
        return pd.DataFrame()

    order_intents = (
        pd.DataFrame(rows)
        .groupby(
            [
                "source_hour",
                "execution_hour",
                "source_hour_ms",
                "address",
                "coin",
                "side",
                "action",
                "source_oid",
            ],
            as_index=False,
        )
        .agg(
            trader_notional=("trader_notional", "sum"),
            source_fills=("source_fills", "sum"),
        )
    )

    keys = [
        "source_hour",
        "execution_hour",
        "source_hour_ms",
        "address",
        "coin",
        "side",
    ]
    notional = pd.DataFrame(
        order_intents.pivot_table(
            index=keys,
            columns="action",
            values="trader_notional",
            aggfunc="sum",
            fill_value=0,
        ).reset_index()
    )
    if "open" not in notional.columns:
        notional["open"] = 0.0
    if "close" not in notional.columns:
        notional["close"] = 0.0
    notional = notional.rename(
        columns={"open": "open_notional", "close": "close_notional"}
    )

    metadata = pd.DataFrame(
        order_intents.groupby(keys, as_index=False).agg(
            source_orders=("source_oid", "nunique"),
            source_fills=("source_fills", "sum"),
            source_oids=(
                "source_oid",
                lambda values: ",".join(map(str, sorted(set(values)))),
            ),
        )
    )
    return notional.merge(metadata, on=keys, how="left")


def _mark_positions(positions, price_lookup, price_history, hour_ms):
    unrealized_pnl = 0.0
    gross_exposure = 0.0
    coins = set()
    price_failure_count = 0
    price_fallback_count = 0

    for position in positions:
        mark_price, used_fallback, _ = u.lookup_price_with_fallback(
            price_lookup,
            price_history,
            position["coin"],
            hour_ms,
        )
        if mark_price is None:
            price_failure_count += 1
            continue
        price_fallback_count += int(used_fallback)
        side_sign = 1 if position["side"] == "long" else -1
        mark_notional = position["qty"] * mark_price
        gross_exposure += mark_notional
        unrealized_pnl += (
            position["qty"] * (mark_price - position["entry_price"]) * side_sign
            - position["open_fee"]
        )
        coins.add(position["coin"])

    return {
        "unrealized_pnl": unrealized_pnl,
        "gross_exposure": gross_exposure,
        "open_position_count": len(positions),
        "open_coin_count": len(coins),
        "price_failure_count": price_failure_count,
        "price_fallback_count": price_fallback_count,
    }


def _close_positions(
    positions,
    address,
    coin,
    side,
    close_qty,
    exit_price,
    hour,
    *,
    close_reason="signal",
):
    closed = []
    remaining_qty = close_qty
    kept_positions = []

    for position in positions:
        if (
            remaining_qty <= 0
            or position["address"] != address
            or position["coin"] != coin
            or position["side"] != side
        ):
            kept_positions.append(position)
            continue

        qty = min(position["qty"], remaining_qty)
        side_sign = 1 if side == "long" else -1
        gross_pnl = qty * (exit_price - position["entry_price"]) * side_sign
        entry_fraction = qty / position["qty"] if position["qty"] else 0
        open_fee = position["open_fee"] * entry_fraction
        exit_notional = qty * exit_price
        exit_fee = exit_notional * position["fee_rate"]
        net_pnl = gross_pnl - open_fee - exit_fee

        closed.append(
            {
                "rebalance_at": position["rebalance_at"],
                "address": address,
                "coin": coin,
                "side": side,
                "opened_at": position["opened_at"],
                "closed_at": hour,
                "entry_price": position["entry_price"],
                "exit_price": exit_price,
                "qty": qty,
                "entry_stake": position["stake"] * entry_fraction,
                "exit_notional": exit_notional,
                "gross_pnl": gross_pnl,
                "open_fee": open_fee,
                "exit_fee": exit_fee,
                "fees": open_fee + exit_fee,
                "net_pnl": net_pnl,
                "close_reason": close_reason,
            }
        )

        position["qty"] -= qty
        position["stake"] -= position["stake"] * entry_fraction
        position["open_fee"] -= open_fee
        remaining_qty -= qty

        if position["qty"] > 1e-12:
            kept_positions.append(position)

    return kept_positions, closed, remaining_qty


def _address_gross_exposure(
    positions,
    address,
    price_lookup,
    price_history,
    hour_ms,
):
    exposure = 0.0
    for position in positions:
        if position["address"] != address:
            continue
        price, _, _ = u.lookup_price_with_fallback(
            price_lookup,
            price_history,
            position["coin"],
            hour_ms,
        )
        if price is None:
            price = position["entry_price"]
        exposure += position["qty"] * price
    return exposure


def _liquidate_positions(positions, price_lookup, price_history, hour, fee_rate):
    closed_trades = []
    failed_trades = []
    remaining_positions = []
    realized_pnl = 0.0
    exit_fees = 0.0
    hour_ms = u.hour_ms(u.timestamp(hour) - pd.Timedelta(hours=1))

    for position in positions:
        exit_price, used_fallback, price_hour_ms = u.lookup_price_with_fallback(
            price_lookup,
            price_history,
            position["coin"],
            hour_ms,
        )
        if exit_price is None:
            remaining_positions.append(position)
            failed_trades.append(
                {
                    "rebalance_at": position["rebalance_at"],
                    "execution_hour": hour,
                    "address": position["address"],
                    "coin": position["coin"],
                    "action": "session_end_close",
                    "side": position["side"],
                    "reason": "missing_price",
                    "hour_ms": hour_ms,
                }
            )
            continue
        _, closed, _ = _close_positions(
            [position],
            position["address"],
            position["coin"],
            position["side"],
            position["qty"],
            exit_price,
            hour,
            close_reason="session_end",
        )
        for trade in closed:
            trade["fee_rate"] = fee_rate
            trade["used_fallback_price"] = used_fallback
            trade["requested_hour_ms"] = hour_ms
            trade["price_hour_ms"] = price_hour_ms
            closed_trades.append(trade)
            realized_pnl += trade["net_pnl"]
            exit_fees += trade["exit_fee"]

    return closed_trades, failed_trades, remaining_positions, realized_pnl, exit_fees


def _add_benchmark_columns(
    equity_curve,
    benchmark_coins,
    starting_capital,
    candle_interval,
    asset_class,
):
    coins = list(benchmark_coins or [])
    if equity_curve.empty or not coins:
        return equity_curve

    out = equity_curve.copy()
    first_source_hour = u.timestamp(out["hour"].iloc[0]) - pd.Timedelta(hours=1)
    last_hour = u.timestamp(out["hour"].iloc[-1])
    candles = q.get_candles(
        coins,
        u.python_datetime(first_source_hour),
        u.python_datetime(last_hour),
        interval=candle_interval,
        asset_class=asset_class,
    )
    price_lookup = u.build_price_lookup(candles)
    price_history = u.build_price_history(candles)
    source_hour_ms = out["hour"].map(
        lambda value: u.hour_ms(u.timestamp(value) - pd.Timedelta(hours=1))
    )

    for coin in coins:
        normalized = coin.lower().replace("-", "_").replace("/", "_")
        value_column = f"benchmark_{normalized}_value"
        return_column = f"benchmark_{normalized}_return"
        prices = [
            u.lookup_price_with_fallback(
                price_lookup, price_history, coin, int(hour_ms)
            )[0]
            for hour_ms in source_hour_ms
        ]
        price_series = pd.Series(prices, index=out.index, dtype="float64")
        first_valid = price_series.dropna()
        if first_valid.empty:
            out[value_column] = pd.NA
            out[return_column] = pd.NA
            continue

        entry_price = float(first_valid.iloc[0])
        out[value_column] = (price_series / entry_price) * starting_capital
        out[return_column] = price_series / entry_price - 1

    return out


def _risk_stats(equity_curve, starting_capital):
    daily = equity_curve[["hour", "portfolio_current_value"]].copy()
    daily["day"] = pd.to_datetime(daily["hour"], utc=True).dt.floor("D")
    daily["portfolio_current_value"] = pd.to_numeric(
        daily["portfolio_current_value"], errors="coerce"
    )
    daily = daily.dropna(subset=["portfolio_current_value"])
    daily = daily.groupby("day", as_index=False).tail(1).reset_index(drop=True)
    if daily.empty:
        return {
            "daily_return_mean": 0.0,
            "daily_return_std": 0.0,
            "daily_downside_return_std": 0.0,
            "daily_sharpe": 0.0,
            "daily_sortino": 0.0,
            "calmar": 0.0,
            "best_day_return": 0.0,
            "worst_day_return": 0.0,
            "positive_day_rate": 0.0,
            "negative_day_rate": 0.0,
            "daily_return_skew": 0.0,
            "daily_return_kurtosis": 0.0,
            "max_drawdown_duration_days": 0,
        }

    values = pd.Series(daily["portfolio_current_value"], dtype="float64").ffill()
    returns = pd.Series(values.pct_change().fillna(0), dtype="float64")
    return_values = u.float_list(returns.tolist())
    downside_return_values = [value for value in return_values if value < 0]
    periods_per_year = 365

    mean_return = u.float_mean(return_values)
    volatility = u.float_sample_std(return_values)
    downside_volatility = u.float_sample_std(downside_return_values)
    sharpe = mean_return / volatility * (periods_per_year**0.5) if volatility else 0.0
    sortino = (
        mean_return / downside_volatility * (periods_per_year**0.5)
        if downside_volatility
        else 0.0
    )
    portfolio_values = u.float_list(values.tolist())
    final_value = portfolio_values[-1] if portfolio_values else starting_capital
    final_return = final_value / starting_capital - 1
    running_peak = values.cummax()
    drawdown = values - running_peak
    drawdown_pct = drawdown / running_peak.replace(0, pd.NA)
    drawdown_pct_values = u.float_list(drawdown_pct.tolist())
    max_drawdown_pct = abs(min(drawdown_pct_values)) if drawdown_pct_values else 0.0
    calmar = final_return / max_drawdown_pct if max_drawdown_pct else 0.0

    max_drawdown_duration_days = 0
    current_duration = 0
    for drawdown_value in u.float_list(drawdown.fillna(0).tolist()):
        if drawdown_value < 0:
            current_duration += 1
            max_drawdown_duration_days = max(
                max_drawdown_duration_days, current_duration
            )
        else:
            current_duration = 0

    positive_days = [value for value in return_values if value > 0]
    negative_days = [value for value in return_values if value < 0]

    return {
        "daily_return_mean": mean_return,
        "daily_return_std": volatility,
        "daily_downside_return_std": downside_volatility,
        "daily_sharpe": sharpe,
        "daily_sortino": sortino,
        "calmar": calmar,
        "best_day_return": max(return_values) if return_values else 0.0,
        "worst_day_return": min(return_values) if return_values else 0.0,
        "positive_day_rate": len(positive_days) / len(return_values)
        if return_values
        else 0.0,
        "negative_day_rate": len(negative_days) / len(return_values)
        if return_values
        else 0.0,
        "daily_return_skew": u.float_skew(return_values),
        "daily_return_kurtosis": u.float_excess_kurtosis(return_values),
        "max_drawdown_duration_days": max_drawdown_duration_days,
    }


def backtest_candidates(
    start_date="2025-07-01",
    end_date=None,
    *,
    days=7,
    n_candidates=50,
    starting_capital=10_000.0,
    compound_portfolio=False,
    candle_interval="1h",
    asset_class="perp",
    leverage=1.0,
    fee_pct=0.0,
    max_trader_exposure_ratio=1.0,
    eligible_only=True,
    benchmark_coins=("BTC", "ETH", "HYPE"),
):
    if candle_interval != "1h":
        raise ValueError("backtest_candidates currently supports candle_interval='1h'")
    if n_candidates <= 0:
        raise ValueError("n_candidates must be > 0")
    if starting_capital <= 0:
        raise ValueError("starting_capital must be > 0")
    if leverage <= 0:
        raise ValueError("leverage must be > 0")
    if max_trader_exposure_ratio is not None and max_trader_exposure_ratio < 0:
        raise ValueError("max_trader_exposure_ratio must be >= 0 or None")

    started_at = time.perf_counter()
    fee_rate = fee_pct / 100
    requested_end_date = end_date
    if end_date is None:
        end_date = q.get_fills_time_bounds()["last_fill_at"]
    effective_end_ts = u.timestamp(end_date)

    all_evaluated = generate_all_evaluated(
        start_date=start_date,
        end_date=end_date,
        days=days,
        exclude_forward_metrics=True,
        asset_class=asset_class,
    )
    if all_evaluated.empty:
        return {
            "selected": pd.DataFrame(),
            "equity_curve": pd.DataFrame(),
            "trade_events": pd.DataFrame(),
            "failed_events": pd.DataFrame(),
            "closed_trades": pd.DataFrame(),
            "open_positions": pd.DataFrame(),
            "stats": {},
        }

    all_scored = score_candidates(all_evaluated)
    ranking_pool = all_scored
    if eligible_only and "eligible_for_ranking" in ranking_pool.columns:
        ranking_pool = ranking_pool[ranking_pool["eligible_for_ranking"]]

    selected = (
        ranking_pool.groupby("rebalance_at", group_keys=False).head(n_candidates).copy()
    )
    if selected.empty:
        return {
            "selected": selected,
            "equity_curve": pd.DataFrame(),
            "trade_events": pd.DataFrame(),
            "failed_events": pd.DataFrame(),
            "closed_trades": pd.DataFrame(),
            "open_positions": pd.DataFrame(),
            "stats": {},
        }

    period_curves = []
    trade_events = []
    failed_events = []
    closed_trades = []
    remaining_positions = []
    history_days = DEFAULT_CONFIG["history_days"]
    portfolio_balance = float(starting_capital)

    for rebalance_at, group in selected.groupby("rebalance_at", sort=True):
        rebalance_ts = u.timestamp(rebalance_at)
        scheduled_session_end = rebalance_ts + pd.Timedelta(days=days)
        session_end = min(scheduled_session_end, effective_end_ts)
        if session_end <= rebalance_ts:
            continue
        session_days = pd.Timedelta(session_end - rebalance_ts).total_seconds() / (
            24 * 60 * 60
        )
        addresses = group["address"].dropna().drop_duplicates().tolist()
        if not addresses:
            continue

        session_start_value = (
            portfolio_balance if compound_portfolio else float(starting_capital)
        )
        trader_budget = session_start_value / len(addresses)
        leveraged_trader_budget = trader_budget * leverage
        trader_exposure_cap = (
            leveraged_trader_budget * max_trader_exposure_ratio
            if max_trader_exposure_ratio
            else None
        )

        history_fills = u.add_fill_buckets(
            q.get_trader_fills(
                addresses,
                rebalance_at=u.python_datetime(rebalance_ts),
                history_days=history_days,
                asset_class=asset_class,
                limit=0,
            )
        )
        forward_fills = u.add_fill_buckets(
            q.get_trader_fills(
                addresses,
                start_at=u.python_datetime(rebalance_ts),
                end_at=u.python_datetime(session_end),
                asset_class=asset_class,
                limit=0,
            )
        )

        history_open_notional = u.history_open_notional_by_address(history_fills)
        scale_by_address = {}
        expected_open_by_address = {}
        for _, row in group.iterrows():
            address = row["address"]
            open_notional = history_open_notional.get(address, 0.0)
            if open_notional <= 0:
                open_notional = u.numeric_value(row, "score_open_notional")
            if open_notional <= 0:
                open_notional = u.numeric_value(row, "score_gross_notional") / 2

            expected_open = open_notional * (session_days / history_days)
            expected_open_by_address[address] = expected_open
            scale_by_address[address] = (
                leveraged_trader_budget / expected_open if expected_open > 0 else 0.0
            )

        selected.loc[group.index, "history_open_notional"] = selected.loc[
            group.index, "address"
        ].map(history_open_notional)
        selected.loc[group.index, "expected_forward_open_notional"] = selected.loc[
            group.index, "address"
        ].map(expected_open_by_address)
        selected.loc[group.index, "copy_scale"] = selected.loc[
            group.index, "address"
        ].map(scale_by_address)
        selected.loc[group.index, "trader_budget"] = trader_budget
        selected.loc[group.index, "leveraged_trader_budget"] = leveraged_trader_budget
        selected.loc[group.index, "portfolio_start_value"] = session_start_value
        selected.loc[group.index, "leverage"] = leverage
        selected.loc[group.index, "max_trader_exposure"] = trader_exposure_cap

        if forward_fills.empty:
            if compound_portfolio:
                portfolio_balance = session_start_value
            continue

        intents = _hourly_trade_intents(forward_fills, session_end)
        coins = forward_fills["coin"].dropna().drop_duplicates().tolist()
        candles = q.get_candles(
            coins,
            u.python_datetime(rebalance_ts),
            u.python_datetime(session_end + pd.Timedelta(hours=1)),
            interval=candle_interval,
            asset_class=asset_class,
        )
        price_lookup = u.build_price_lookup(candles)
        price_history = u.build_price_history(candles)

        positions = []
        realized_pnl = 0.0
        fees_paid = 0.0
        hours = pd.date_range(
            start=rebalance_ts + pd.Timedelta(hours=1),
            end=session_end,
            freq=candle_interval,
            inclusive="left",
        )

        for hour in hours:
            execution_hour = u.timestamp(hour)
            source_hour = execution_hour - pd.Timedelta(hours=1)
            source_hour_ms = u.hour_ms(source_hour)
            hour_intents = (
                pd.DataFrame()
                if intents.empty
                else intents[intents["execution_hour"] == execution_hour]
            )
            trades_opened = 0
            trades_closed = 0
            ignored_same_hour_crosses = 0
            stake_opened = 0.0
            stake_closed = 0.0
            requested_stake_opened = 0.0
            clipped_stake_opened = 0.0
            clipped_open_count = 0
            skipped_open_count = 0

            for _, intent in hour_intents.iterrows():
                address = intent["address"]
                scale = scale_by_address.get(address, 0.0)
                if scale <= 0:
                    continue

                coin = str(intent["coin"])
                side = str(intent["side"])
                open_notional = u.numeric_value(intent, "open_notional")
                close_notional = u.numeric_value(intent, "close_notional")
                ignored_notional = min(open_notional, close_notional)
                ignored_same_hour_crosses += int(ignored_notional > 0)
                net_open_notional = max(open_notional - close_notional, 0)
                net_close_notional = max(close_notional - open_notional, 0)

                price, used_fallback_price, price_hour_ms = (
                    u.lookup_price_with_fallback(
                        price_lookup,
                        price_history,
                        coin,
                        source_hour_ms,
                    )
                )
                if price is None:
                    failed_events.append(
                        {
                            "rebalance_at": rebalance_at,
                            "source_hour": intent["source_hour"],
                            "execution_hour": execution_hour,
                            "address": address,
                            "coin": coin,
                            "action": "signal",
                            "side": side,
                            "reason": "missing_price",
                            "source_orders": intent["source_orders"],
                            "source_fills": intent["source_fills"],
                            "source_oids": intent["source_oids"],
                            "requested_hour_ms": source_hour_ms,
                        }
                    )
                    continue

                if net_close_notional > 0:
                    requested_stake = net_close_notional * scale
                    requested_qty = requested_stake / price
                    positions, closed, remaining_qty = _close_positions(
                        positions,
                        address,
                        coin,
                        side,
                        requested_qty,
                        price,
                        execution_hour,
                    )
                    matched_qty = requested_qty - max(remaining_qty, 0)

                    for closed_trade in closed:
                        closed_trade["source_hour"] = intent["source_hour"]
                        closed_trade["source_oids"] = intent["source_oids"]
                        closed_trade["fee_rate"] = fee_rate
                        closed_trade["used_fallback_price"] = used_fallback_price
                        closed_trade["requested_hour_ms"] = source_hour_ms
                        closed_trade["price_hour_ms"] = price_hour_ms
                        closed_trades.append(closed_trade)
                        realized_pnl += closed_trade["net_pnl"]
                        fees_paid += closed_trade["exit_fee"]
                        trades_closed += 1
                        stake_closed += closed_trade["exit_notional"]

                    if matched_qty > 0:
                        trade_events.append(
                            {
                                "rebalance_at": rebalance_at,
                                "source_hour": intent["source_hour"],
                                "execution_hour": execution_hour,
                                "address": address,
                                "coin": coin,
                                "action": "close",
                                "side": side,
                                "price": price,
                                "qty": matched_qty,
                                "stake": matched_qty * price,
                                "requested_stake": requested_stake,
                                "unmatched_stake": max(remaining_qty, 0) * price,
                                "source_orders": intent["source_orders"],
                                "source_fills": intent["source_fills"],
                                "source_oids": intent["source_oids"],
                                "used_fallback_price": used_fallback_price,
                                "requested_hour_ms": source_hour_ms,
                                "price_hour_ms": price_hour_ms,
                            }
                        )

                if net_open_notional > 0:
                    requested_stake = net_open_notional * scale
                    trader_exposure_before = _address_gross_exposure(
                        positions,
                        address,
                        price_lookup,
                        price_history,
                        source_hour_ms,
                    )
                    if trader_exposure_cap is None:
                        available_trader_exposure = requested_stake
                    else:
                        available_trader_exposure = max(
                            trader_exposure_cap - trader_exposure_before,
                            0.0,
                        )
                    stake = min(requested_stake, available_trader_exposure)
                    clipped_stake = max(requested_stake - stake, 0.0)
                    requested_stake_opened += requested_stake
                    clipped_stake_opened += clipped_stake
                    clipped_open_count += int(clipped_stake > 1e-12 and stake > 1e-12)
                    if stake <= 1e-12:
                        skipped_open_count += 1
                        continue

                    qty = stake / price
                    open_fee = stake * fee_rate
                    fees_paid += open_fee
                    positions.append(
                        {
                            "rebalance_at": rebalance_at,
                            "address": address,
                            "coin": coin,
                            "side": side,
                            "opened_at": execution_hour,
                            "source_hour": intent["source_hour"],
                            "entry_price": price,
                            "qty": qty,
                            "stake": stake,
                            "open_fee": open_fee,
                            "fee_rate": fee_rate,
                            "source_oids": intent["source_oids"],
                            "source_orders": intent["source_orders"],
                            "source_fills": intent["source_fills"],
                            "used_fallback_price": used_fallback_price,
                            "requested_hour_ms": source_hour_ms,
                            "price_hour_ms": price_hour_ms,
                            "requested_stake": requested_stake,
                            "clipped_stake": clipped_stake,
                            "trader_exposure_before": trader_exposure_before,
                            "trader_exposure_cap": trader_exposure_cap,
                        }
                    )
                    trades_opened += 1
                    stake_opened += stake
                    trade_events.append(
                        {
                            "rebalance_at": rebalance_at,
                            "source_hour": intent["source_hour"],
                            "execution_hour": execution_hour,
                            "address": address,
                            "coin": coin,
                            "action": "open",
                            "side": side,
                            "price": price,
                            "qty": qty,
                            "stake": stake,
                            "source_orders": intent["source_orders"],
                            "source_fills": intent["source_fills"],
                            "source_oids": intent["source_oids"],
                            "ignored_same_hour_notional": ignored_notional * scale,
                            "used_fallback_price": used_fallback_price,
                            "requested_hour_ms": source_hour_ms,
                            "price_hour_ms": price_hour_ms,
                            "requested_stake": requested_stake,
                            "clipped_stake": clipped_stake,
                            "trader_exposure_before": trader_exposure_before,
                            "trader_exposure_cap": trader_exposure_cap,
                        }
                    )

            mark = _mark_positions(
                positions,
                price_lookup,
                price_history,
                source_hour_ms,
            )
            equity_pnl = realized_pnl + mark["unrealized_pnl"]
            period_curves.append(
                {
                    "rebalance_at": rebalance_at,
                    "hour": execution_hour,
                    "portfolio_start_value": session_start_value,
                    "portfolio_current_value": session_start_value + equity_pnl,
                    "portfolio_return": equity_pnl / session_start_value,
                    "selected_traders": len(addresses),
                    "allocated_capital": session_start_value,
                    "gross_capital_limit": session_start_value * leverage,
                    "realized_pnl": realized_pnl,
                    "unrealized_pnl": mark["unrealized_pnl"],
                    "equity_pnl": equity_pnl,
                    "gross_exposure": mark["gross_exposure"],
                    "open_position_count": mark["open_position_count"],
                    "open_coin_count": mark["open_coin_count"],
                    "trades_opened": trades_opened,
                    "trades_closed": trades_closed,
                    "ignored_same_hour_crosses": ignored_same_hour_crosses,
                    "stake_opened": stake_opened,
                    "stake_closed": stake_closed,
                    "requested_stake_opened": requested_stake_opened,
                    "clipped_stake_opened": clipped_stake_opened,
                    "clipped_open_count": clipped_open_count,
                    "skipped_open_count": skipped_open_count,
                    "fees_paid": fees_paid,
                    "price_failure_count": mark["price_failure_count"],
                    "price_fallback_count": mark["price_fallback_count"],
                }
            )

        if positions:
            (
                liquidation_trades,
                liquidation_failed,
                positions,
                liquidation_pnl,
                liquidation_fees,
            ) = _liquidate_positions(
                positions,
                price_lookup,
                price_history,
                session_end,
                fee_rate,
            )
            failed_events.extend(liquidation_failed)
            for trade in liquidation_trades:
                closed_trades.append(trade)
                trade_events.append(
                    {
                        "rebalance_at": rebalance_at,
                        "source_hour": pd.NaT,
                        "execution_hour": session_end,
                        "address": trade["address"],
                        "coin": trade["coin"],
                        "action": "session_end_close",
                        "side": trade["side"],
                        "price": trade["exit_price"],
                        "qty": trade["qty"],
                        "stake": trade["exit_notional"],
                        "source_orders": 0,
                        "source_fills": 0,
                        "source_oids": "",
                        "used_fallback_price": trade.get("used_fallback_price"),
                        "requested_hour_ms": trade.get("requested_hour_ms"),
                        "price_hour_ms": trade.get("price_hour_ms"),
                    }
                )

            realized_pnl += liquidation_pnl
            fees_paid += liquidation_fees

            period_curves.append(
                {
                    "rebalance_at": rebalance_at,
                    "hour": session_end,
                    "portfolio_start_value": session_start_value,
                    "portfolio_current_value": session_start_value + realized_pnl,
                    "portfolio_return": realized_pnl / session_start_value,
                    "selected_traders": len(addresses),
                    "allocated_capital": session_start_value,
                    "gross_capital_limit": session_start_value * leverage,
                    "realized_pnl": realized_pnl,
                    "unrealized_pnl": 0.0,
                    "equity_pnl": realized_pnl,
                    "gross_exposure": 0.0,
                    "open_position_count": 0,
                    "open_coin_count": 0,
                    "trades_opened": 0,
                    "trades_closed": len(liquidation_trades),
                    "ignored_same_hour_crosses": 0,
                    "stake_opened": 0.0,
                    "stake_closed": sum(t["exit_notional"] for t in liquidation_trades),
                    "requested_stake_opened": 0.0,
                    "clipped_stake_opened": 0.0,
                    "clipped_open_count": 0,
                    "skipped_open_count": 0,
                    "fees_paid": fees_paid,
                    "price_failure_count": len(liquidation_failed),
                    "price_fallback_count": sum(
                        int(t.get("used_fallback_price", False))
                        for t in liquidation_trades
                    ),
                }
            )

        if compound_portfolio:
            portfolio_balance = session_start_value + realized_pnl
        remaining_positions.extend(positions)

    period_equity_curve = pd.DataFrame(period_curves)
    if period_equity_curve.empty:
        equity_curve = pd.DataFrame()
        stats = {}
    else:
        period_equity_curve = (
            period_equity_curve.set_index("hour").sort_index().reset_index()
        )
        if compound_portfolio:
            period_equity_curve["carried_pnl"] = (
                period_equity_curve["portfolio_start_value"] - starting_capital
            )
        else:
            final_by_rebalance = (
                period_equity_curve.groupby("rebalance_at", as_index=False)
                .tail(1)[["rebalance_at", "equity_pnl"]]
                .reset_index(drop=True)
            )
            final_by_rebalance["carried_pnl"] = (
                final_by_rebalance["equity_pnl"].cumsum()
                - final_by_rebalance["equity_pnl"]
            )
            carried_by_rebalance = dict(
                zip(
                    final_by_rebalance["rebalance_at"],
                    final_by_rebalance["carried_pnl"],
                    strict=False,
                )
            )
            period_equity_curve["carried_pnl"] = period_equity_curve[
                "rebalance_at"
            ].apply(lambda value: carried_by_rebalance.get(value, 0.0))

        period_equity_curve["portfolio_current_value"] = (
            starting_capital
            + period_equity_curve["carried_pnl"]
            + period_equity_curve["equity_pnl"]
        )
        period_equity_curve["portfolio_return"] = (
            period_equity_curve["portfolio_current_value"] / starting_capital - 1
        )
        equity_curve = period_equity_curve.copy()
        equity_curve["equity_return"] = equity_curve["equity_pnl"] / equity_curve[
            "allocated_capital"
        ].replace(0, pd.NA)
        equity_curve["realized_return"] = equity_curve["realized_pnl"] / equity_curve[
            "allocated_capital"
        ].replace(0, pd.NA)
        equity_curve["running_peak_portfolio_value"] = equity_curve[
            "portfolio_current_value"
        ].cummax()
        equity_curve["drawdown"] = (
            equity_curve["portfolio_current_value"]
            - equity_curve["running_peak_portfolio_value"]
        )
        equity_curve = _add_benchmark_columns(
            equity_curve,
            benchmark_coins,
            starting_capital,
            candle_interval,
            asset_class,
        )

        closed_trades_df = pd.DataFrame(closed_trades)
        trade_events_df = pd.DataFrame(trade_events)
        total_stake_opened = u.series_sum(equity_curve, "stake_opened")
        total_stake_closed = u.series_sum(equity_curve, "stake_closed")
        total_requested_stake_opened = u.series_sum(
            equity_curve,
            "requested_stake_opened",
        )
        total_clipped_stake_opened = u.series_sum(
            equity_curve,
            "clipped_stake_opened",
        )
        clipped_open_count = int(u.series_sum(equity_curve, "clipped_open_count"))
        skipped_open_count = int(u.series_sum(equity_curve, "skipped_open_count"))
        total_volume = total_stake_opened + total_stake_closed
        final_portfolio_value = u.series_last(equity_curve, "portfolio_current_value")
        final_portfolio_return = u.series_last(equity_curve, "portfolio_return")
        max_drawdown = abs(u.series_min(equity_curve, "drawdown"))
        gross_exposure_mean = u.series_mean(equity_curve, "gross_exposure")
        gross_exposure_max = u.series_max(equity_curve, "gross_exposure")
        avg_gross_exposure_ratio = gross_exposure_mean / starting_capital
        max_gross_exposure_ratio = gross_exposure_max / starting_capital
        gross_exposure_series = pd.Series(
            pd.to_numeric(equity_curve["gross_exposure"], errors="coerce"),
            index=equity_curve.index,
        ).fillna(0)
        portfolio_value_series = pd.Series(
            pd.to_numeric(equity_curve["portfolio_current_value"], errors="coerce"),
            index=equity_curve.index,
        )
        portfolio_value_series = portfolio_value_series.mask(
            portfolio_value_series == 0,
            pd.NA,
        )
        current_exposure_ratio_series = gross_exposure_series / portfolio_value_series
        current_exposure_ratios = u.float_list(current_exposure_ratio_series.tolist())
        avg_gross_exposure_ratio_on_current_equity = u.float_mean(
            current_exposure_ratios
        )
        max_gross_exposure_ratio_on_current_equity = (
            max(current_exposure_ratios) if current_exposure_ratios else 0.0
        )
        active_hours = int((equity_curve["gross_exposure"] > 0).sum())
        total_hours = int(len(equity_curve))
        active_hour_rate = active_hours / total_hours if total_hours else 0
        risk_stats = _risk_stats(equity_curve, starting_capital)

        if closed_trades_df.empty:
            winning_closed_trades = 0
            losing_closed_trades = 0
            closed_trade_win_rate = 0
            gross_profit = 0.0
            gross_loss = 0.0
            profit_factor = 0.0
        else:
            closed_net_pnl = pd.Series(
                pd.to_numeric(closed_trades_df["net_pnl"], errors="coerce")
            ).fillna(0)
            winning_closed_trades = int((closed_net_pnl > 0).sum())
            losing_closed_trades = int((closed_net_pnl < 0).sum())
            closed_trade_win_rate = (
                winning_closed_trades / (winning_closed_trades + losing_closed_trades)
                if winning_closed_trades + losing_closed_trades
                else 0
            )
            gross_profit = float(closed_net_pnl[closed_net_pnl > 0].sum())
            gross_loss = float(abs(closed_net_pnl[closed_net_pnl < 0].sum()))
            profit_factor = gross_profit / gross_loss if gross_loss else 0.0

        final_net_pnl = final_portfolio_value - starting_capital
        net_pnl_on_total_volume = final_net_pnl / total_volume if total_volume else 0.0
        net_pnl_on_total_stake_opened = (
            final_net_pnl / total_stake_opened if total_stake_opened else 0.0
        )

        if trade_events_df.empty:
            unique_coins_traded = 0
            unique_traders_copied = 0
            session_end_closes = 0
            fallback_trade_events = 0
        else:
            unique_coins_traded = len(set(trade_events_df["coin"].dropna().tolist()))
            unique_traders_copied = len(
                set(trade_events_df["address"].dropna().tolist())
            )
            action_series = pd.Series(trade_events_df["action"])
            session_end_closes = int((action_series == "session_end_close").sum())
            fallback_trade_events = int(
                pd.Series(trade_events_df.get("used_fallback_price", False))
                .fillna(False)
                .astype(bool)
                .sum()
            )

        actual_end_date = equity_curve["hour"].iloc[-1]
        if not trade_events_df.empty:
            execution_hours = pd.to_datetime(
                trade_events_df["execution_hour"], utc=True, errors="coerce"
            ).dropna()
            if not execution_hours.empty:
                actual_end_date = execution_hours.max()

        stats = {
            "start_date": str(equity_curve["hour"].iloc[0]),
            "end_date": str(actual_end_date),
            "equity_curve_end_date": str(equity_curve["hour"].iloc[-1]),
            "requested_start_date": str(start_date),
            "requested_end_date": None
            if requested_end_date is None
            else str(requested_end_date),
            "effective_end_date": str(effective_end_ts),
            "days": days,
            "n_candidates": n_candidates,
            "starting_capital": starting_capital,
            "compound_portfolio": compound_portfolio,
            "leverage": leverage,
            "fee_pct": fee_pct,
            "max_trader_exposure_ratio": max_trader_exposure_ratio,
            "benchmark_coins": list(benchmark_coins or []),
            "selected_rows": len(selected),
            "unique_rebalance_dates": int(selected["rebalance_at"].nunique()),
            "unique_traders_selected": int(selected["address"].nunique()),
            "unique_traders_copied": unique_traders_copied,
            "unique_coins_traded": unique_coins_traded,
            "closed_trades": len(closed_trades),
            "winning_closed_trades": winning_closed_trades,
            "losing_closed_trades": losing_closed_trades,
            "closed_trade_win_rate": closed_trade_win_rate,
            "gross_profit": gross_profit,
            "gross_loss": gross_loss,
            "profit_factor": profit_factor,
            "open_positions_remaining": len(remaining_positions),
            "session_end_closes": session_end_closes,
            "final_portfolio_value": final_portfolio_value,
            "final_portfolio_return": final_portfolio_return,
            "final_net_pnl": final_net_pnl,
            **risk_stats,
            "max_drawdown": max_drawdown,
            "max_drawdown_pct": max_drawdown / starting_capital,
            "total_stake_opened": total_stake_opened,
            "total_stake_closed": total_stake_closed,
            "total_requested_stake_opened": total_requested_stake_opened,
            "total_clipped_stake_opened": total_clipped_stake_opened,
            "clipped_open_count": clipped_open_count,
            "skipped_open_count": skipped_open_count,
            "clipped_stake_opened_rate": (
                total_clipped_stake_opened / total_requested_stake_opened
                if total_requested_stake_opened
                else 0.0
            ),
            "total_volume": total_volume,
            "turnover_on_starting_capital": total_volume / starting_capital,
            "net_pnl_on_total_volume": net_pnl_on_total_volume,
            "net_pnl_on_total_stake_opened": net_pnl_on_total_stake_opened,
            "avg_gross_exposure": gross_exposure_mean,
            "max_gross_exposure": gross_exposure_max,
            "avg_gross_exposure_ratio": avg_gross_exposure_ratio,
            "max_gross_exposure_ratio": max_gross_exposure_ratio,
            "avg_gross_exposure_ratio_on_current_equity": avg_gross_exposure_ratio_on_current_equity,
            "max_gross_exposure_ratio_on_current_equity": max_gross_exposure_ratio_on_current_equity,
            "avg_open_position_count": u.series_mean(
                equity_curve, "open_position_count"
            ),
            "max_open_position_count": int(
                u.series_max(equity_curve, "open_position_count")
            ),
            "avg_open_coin_count": u.series_mean(equity_curve, "open_coin_count"),
            "max_open_coin_count": int(u.series_max(equity_curve, "open_coin_count")),
            "active_hours": active_hours,
            "total_hours": total_hours,
            "active_hour_rate": active_hour_rate,
            "total_fees_paid": u.series_last(equity_curve, "fees_paid"),
            "failed_events": len(failed_events),
            "price_failure_count": int(
                u.series_sum(equity_curve, "price_failure_count")
            ),
            "price_fallback_count": int(
                u.series_sum(equity_curve, "price_fallback_count")
            ),
            "fallback_trade_events": fallback_trade_events,
            "backtest_elapsed_seconds": round(time.perf_counter() - started_at, 3),
        }
        for coin in benchmark_coins or []:
            normalized = coin.lower().replace("-", "_").replace("/", "_")
            return_column = f"benchmark_{normalized}_return"
            value_column = f"benchmark_{normalized}_value"
            if return_column in equity_curve.columns:
                benchmark_returns = u.float_list(equity_curve[return_column].tolist())
                stats[f"{return_column}_final"] = (
                    benchmark_returns[-1] if benchmark_returns else None
                )
            if value_column in equity_curve.columns:
                benchmark_values = u.float_list(equity_curve[value_column].tolist())
                stats[f"{value_column}_final"] = (
                    benchmark_values[-1] if benchmark_values else None
                )

    return {
        "selected": selected.reset_index(drop=True),
        "equity_curve": equity_curve,
        "trade_events": pd.DataFrame(trade_events),
        "failed_events": pd.DataFrame(failed_events),
        "closed_trades": pd.DataFrame(closed_trades),
        "open_positions": pd.DataFrame(remaining_positions),
        "stats": stats,
    }


if __name__ == "__main__":
    all_evaluated = generate_all_evaluated()
    all_evaluated.to_csv("../reports/all_evaluated.csv", index=False)
    print("Elapsed Seconds:", all_evaluated.iloc[0]["evaluation_elapsed_seconds"])

    all_scored = score_candidates(all_evaluated)
    all_scored.to_csv("../reports/all_scored.csv", index=False)

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

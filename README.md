# Project Mosaic

## Hyperliquid Data

### Node Fills

```text
s3://hl-mainnet-node-data/node_fills/hourly/
```

* Starts: `2025-05-25`
* Ends: `2025-07-27`

Check object count and total size:

```bash
aws s3 ls s3://hl-mainnet-node-data/node_fills/hourly/ \
  --recursive \
  --summarize \
  --human-readable \
  --request-payer requester | tail -n 2
```

```text
Total Objects: 1507
   Total Size: 29.6 GiB
```

### Node Fills by Block

```text
s3://hl-mainnet-node-data/node_fills_by_block/hourly/
```

* Starts: `2025-07-27`

Check object count and total size:

```bash
aws s3 ls s3://hl-mainnet-node-data/node_fills_by_block/hourly/ \
  --recursive \
  --summarize \
  --human-readable \
  --request-payer requester | tail -n 2
```

```text
Total Objects: 8057
   Total Size: 198.5 GiB
```

Check object count and total size for `2025` data only:

```bash
aws s3 ls s3://hl-mainnet-node-data/node_fills_by_block/hourly/ \
  --recursive \
  --request-payer requester \
| awk '
  $3 ~ /^[0-9]+$/ && $NF ~ /(^|\/)2025[0-9][0-9][0-9][0-9]\// {
    objs++;
    bytes += $3
  }
  END {
    print "Total Objects:", objs + 0;
    printf "Total Size: %.2f GiB\n", bytes / 1024 / 1024 / 1024
  }
'
```

```text
Total Objects: 3784
Total Size: 86.20 GiB
```

### Download Node Fills

```bash
aws s3 sync s3://hl-mainnet-node-data/node_fills/hourly/ \
  ./data/node_fills/ \
  --request-payer requester
```

### Download Node Fills By Block for 2026 Only

```bash
aws s3 sync s3://hl-mainnet-node-data/node_fills_by_block/hourly/ \
  ./data/node_fills_by_block/ \
  --exclude "*" \
  --include "2026*/*" \
  --request-payer requester
```

### Data Format

Each `node_fills` record is a fill event encoded as:

```jsonc
[
  "string", // trader/user address
  {
    "coin": "string",          // market symbol, e.g. BTC
    "px": "decimal string",    // fill price
    "sz": "decimal string",    // fill size
    "side": "string",          // B = buy, A = sell
    "time": "integer",         // fill timestamp in milliseconds
    "startPosition": "decimal string", // position before/at fill
    "dir": "string",           // trade direction, e.g. Open Long
    "closedPnl": "decimal string", // realized PnL from this fill
    "hash": "string",          // transaction hash
    "oid": "integer",          // order id
    "crossed": "boolean",      // whether liquidity was crossed
    "fee": "decimal string",   // fee amount
    "tid": "integer",          // trade id
    "feeToken": "string",      // fee token, e.g. USDC
    "twapId": "integer | null" // TWAP id, if associated with a TWAP order
  }
]
```

Each `node_fills_by_block` record groups fill events by block:

```jsonc
{
  "local_time": "timestamp string", // local ingestion timestamp
  "block_time": "timestamp string", // block timestamp
  "block_number": "integer",        // block number
  "events": []                      // array of fill events in the same format as node_fills
}
```

### ClickHouse Ingestion

Fill data can be ingested into ClickHouse with:

```bash
python3 scripts/ingest_lz4.py data/node_fills
python3 scripts/ingest_lz4.py data/node_fills_by_block
```

The script normalizes fill events, inserts them into `hyperliquid.fills`, and records completed files in `hyperliquid.ingested_files` so reruns skip already-ingested data.

```sql clickhouse
address         String                  -- trader/user address
coin            LowCardinality(String)  -- market symbol, e.g. BTC, PURR/USDC, @107
asset_class     LowCardinality(String)  -- asset class, e.g. perp or spot
px              Decimal(20, 10)         -- fill price
sz              Decimal(20, 10)         -- fill size
side            LowCardinality(String)  -- B = buy, A = sell
time            UInt64                  -- fill timestamp in milliseconds
start_position  Decimal(20, 10)         -- position before fill
dir             LowCardinality(String)  -- trade direction, e.g. Open Long
closed_pnl      Decimal(20, 10)         -- realized PnL from this fill
hash            String                  -- transaction hash
oid             UInt64                  -- order id
crossed         Bool                    -- whether liquidity was crossed
fee             Decimal(20, 10)         -- fee amount
tid             UInt64                  -- trade id
fee_token       LowCardinality(String)  -- fee token, e.g. USDC
twap_id         Nullable(UInt64)        -- TWAP id, if fill is associated with a TWAP order
```

`asset_class` is derived from the `coin` value. A fill is `spot` if `coin` is `PURR/USDC` or an `@`-prefixed spot pair ID like `@107`; otherwise it is `perp`.

For the full set of `dir` / direction values, see Hydromancer's schema reference: https://docs.hydromancer.xyz/reservoir/schema-reference/fills#direction-values

## Candidate Selection

The basic loop is:

1. Pick a `rebalance_at` date.
2. Find traders active in the previous week.
3. Pull their previous 30 days of fills.
4. Keep profitable traders using net PnL: `closed_pnl - fee`.
5. Apply simple candidate filters to reduce noisy, high-frequency, or hard-to-copy traders.
6. Evaluate historical `score_*` metrics and 7-day `forward_*` outcomes.
7. Rank candidates with `score_candidates()`.

Example:

```python
import v0

r = v0.load(rebalance_at="2025-07-01")
raw = r["candidates"]

filtered = v0.filter_candidates(raw)
evaluated = v0.evaluate_candidates(filtered, rebalance_at="2025-07-01")
scored = v0.score_candidates(evaluated)

best = scored[scored["eligible_for_ranking"]].head(50)
```

`generate_all_evaluated()` runs the same flow across weekly rebalance dates and writes `reports/all_evaluated.csv` when `notebooks/v0.py` is run directly.

## Scoring Notes

Only historical `score_*` columns should be used for ranking. `forward_*` columns are labels for validation/backtesting, not inputs.

The current score uses percentile ranks within each `rebalance_at` group:

```text
quality =
    0.30 * P(score_realized_win_rate)
  + 0.25 * average(P(score_sharpe), P(score_consistency_adjusted_return))
  + 0.20 * P(score_return_mean)
  + 0.05 * P(score_positive_day_rate)

risk_penalty =
    0.15 * P(score_max_drawdown_on_notional)
  + 0.10 * P(score_fee_rate_on_notional)
  + 0.05 * P(score_crossed_fill_rate)
  + 0.05 * P(score_day_profit_concentration)

candidate_score = quality - risk_penalty
```

`candidate_rank` is assigned within each rebalance group after sorting by:

1. `rebalance_at`
2. `eligible_for_ranking`
3. `candidate_score_guarded`
4. `candidate_score`

## Current Learnings

The strongest clean historical signal so far is `score_realized_win_rate`: it predicts higher forward return and lower forward drawdown.

Useful positive signals:

* `score_realized_win_rate`
* `score_sharpe`
* `score_consistency_adjusted_return`
* `score_return_mean`
* `score_positive_day_rate`

Useful risk or copy-trading penalty signals:

* `score_max_drawdown_on_notional`
* `score_downside_return_std`
* `score_fee_rate_on_notional`
* `score_crossed_fill_rate`
* `score_day_profit_concentration`

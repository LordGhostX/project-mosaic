# Mosaic Strategy

## Hyperliquid Fills Data

### Node Fills

```text
s3://hl-mainnet-node-data/node_fills/hourly/
```

* Starts: `2025-05-25`
* Ends: `2025-07-27`

Check object count and total size:

```shell
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

```shell
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

```shell
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

```shell
aws s3 sync s3://hl-mainnet-node-data/node_fills/hourly/ \
  ./data/node_fills/ \
  --request-payer requester
```

### Download Node Fills by Block for 2025 Only

```shell
aws s3 sync s3://hl-mainnet-node-data/node_fills_by_block/hourly/ \
  ./data/node_fills_by_block/ \
  --exclude "*" \
  --include "2025*/*" \
  --request-payer requester
```

### Extracting LZ4 Files

Decompress all `.lz4` files under `./data/`:

```shell
find ./data/ -type f -name '*.lz4' \
  -exec sh -c 'lz4 -d -f "$1" "${1%.lz4}"' _ {} \;
```

Delete the compressed `.lz4` files after successful extraction:

```shell
find ./data/ -type f -name '*.lz4' -delete
```

### Loading into ClickHouse

For loading `.lz4` files into ClickHouse, see:

```text
https://clickhouse.com/docs/sql-reference/statements/insert-into#inserting-data-from-a-file
```

## Data Format

Each `node fills` record is a fill event encoded as:

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
    "feeToken": "string"       // fee token, e.g. USDC
  }
]
```

Each `node fills by block` record groups fill events by block:

```jsonc
{
  "local_time": "timestamp string", // local ingestion timestamp
  "block_time": "timestamp string", // block timestamp
  "block_number": "integer",        // block number
  "events": [
    [
      "string", // trader/user address
      {
        "coin": "string",          // market symbol, e.g. SUI
        "px": "decimal string",    // fill price
        "sz": "decimal string",    // fill size
        "side": "string",          // B = buy, A = sell
        "time": "integer",         // fill timestamp in milliseconds
        "startPosition": "decimal string", // position before/at fill
        "dir": "string",           // trade direction, e.g. Open Short
        "closedPnl": "decimal string", // realized PnL from this fill
        "hash": "string",          // transaction hash
        "oid": "integer",          // order id
        "crossed": "boolean",      // whether liquidity was crossed
        "fee": "decimal string",   // fee amount
        "tid": "integer",          // trade id
        "cloid": "string",         // optional client order id
        "feeToken": "string"       // fee token, e.g. USDC
      }
    ]
  ]
}
```

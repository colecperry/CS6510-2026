# Self-Checkout, Pipes and Filters (Week 4)

The same self-checkout API contract from `../spec/self-checkout-openapi.yaml`, with the windowed analytics rebuilt as a pipeline.

Checkout itself is request and response: a customer scans, and the till must answer immediately.
There is nothing to pipeline there.
The analytics is different - every scan feeds a stream, the stream is cut into windows, and each window produces a ranking.
That is a pipeline described in words, and this week it became one.

Built on [week 2's layered implementation](../layered), which is otherwise unchanged, so the numbers below compare like with like.

## The pipeline

**The system has a 3 stage pipeline:**

```
scan -> [ingest] -> [window] -> [ranker] -> published ranking
```

| Filter | Purpose |
|---|---|
| **ingest** | Buffers incoming scans and writes them to `scan_log` in batches, emitting the highest sequence number it just used. |
| **window** | Decides whether enough scans have passed for a new ranking, and if so emits the stretch of scans to count. |
| **ranker** | Counts scans per item inside that stretch, publishes the new ranking, and prunes scans too old to count again. |

**The pipes are Python `asyncio.Queue` objects**, one between each pair of filters.
Each filter is an `asyncio` task running a loop: take from the pipe behind it, do one job, put the result in the pipe in front.
No filter calls another, which is what makes them separate pieces rather than one function split three ways.

What each pipe carries:

| Pipe | Carries | Example |
|---|---|---|
| `_scans` | a barcode | `"SKU-000001"` |
| `_sequences` | how far the stream has got | `600` |
| `_windows` | the stretch to count | `(0, 600)` |

Each stage hands on less data and more meaning.
Raw barcodes become a position in the stream, which becomes a decision about what to count.

Why `asyncio.Queue` rather than threads: the whole application is already `asyncio`, and `asyncpg` connections are not thread-safe.
Threads would have meant either a second connection pool or cross-thread hand-offs, for no benefit.

All of it lives in [app/analytics/pipeline.py](app/analytics/pipeline.py), about 160 lines.

## What changed, and why it is faster

Before, every scan did two database round trips while the customer waited:

```python
seq = await self._analytics.log_scan(sku)            # trip 1: write the scan
window = await self._analytics.claim_recompute(seq)  # trip 2: is a rebuild due?
```

Neither affects the response.
Now the scan is handed to a queue and the request ends:

```python
self._pipeline.submit(sku)      # no await, no database
```

Three effects, all pulling the same way:

- **The request stops waiting.** Handing a scan to a queue takes about 0.23 microseconds. The database write it replaced took around 1,000.
- **Round trips collapse.** One `INSERT` per scan became one per batch. At 1,300 scans a second that is roughly 1,300 statements replaced by a few dozen.
- **Contention drops.** The "is a rebuild due?" check updates a single shared row. It now runs once per batch instead of once per scan.

### Two correctness improvements that came for free

Worth noting because they are consequences of the architecture, not fixes:

- **A race disappeared.** Previously 100 concurrent requests wrote to `scan_log` independently, so when a window was counted, some rows below its end had not committed yet. They were counted as zero and then pruned unseen. One ingest filter means writes are serialised, so everything inside a window is committed before it is counted.
- **Publishing is serialised by construction.** `popularity_snapshot.rank` is a primary key, so two concurrent publishes would collide. Previously only the mutual exclusion inside the due-check prevented that. Now there is one ranker, so there is only ever one publisher.

## Results

Same client, same parameters, same machine as the previous weeks.
Reports in `reports/`.

### Default (10 stations, 60s)

| | monolith | layered | pipeline |
|---|---|---|---|
| Transactions/sec | 98.3 | 99.7 | **123.6** |
| Items/sec | 1,032.6 | 1,048.2 | **1,300.9** |
| SCAN_ITEM p95 | 9.68ms | 9.49ms | **7.10ms** |
| SCAN_ITEM p99 | 22.02ms | 20.64ms | **13.38ms** |
| COMPLETE p95 | 21.48ms | 20.95ms | 31.95ms |
| Error rate | 0.00% | 0.00% | 0.00% |

### Stress (100 stations, 120s)

| | monolith | layered | pipeline |
|---|---|---|---|
| Transactions/sec | 117.0 | 115.8 | **124.5** |
| Items/sec | 1,222.2 | 1,213.4 | **1,313.7** |
| SCAN_ITEM p95 | 18.39ms | 19.37ms | **10.88ms** |
| SCAN_ITEM p99 | 55.45ms | 52.85ms | **36.36ms** |
| COMPLETE p95 | 2,722.91ms | 2,712.42ms | 2,726.74ms |
| Error rate | 0.00% | 0.02% | 0.02% |

### Reading these numbers

**The scan path got substantially faster**, which is what the pipeline was for.
SCAN_ITEM p95 fell 25% at default load and 44% under stress.
The gain is larger under stress because that is when contention on the shared bookkeeping row hurt most, and the pipeline now touches it once per batch rather than once per scan.

Throughput followed: 24% more transactions per second at default load.

**Completion got slower at default load**, from 20.95ms to 31.95ms at p95.
This was not predicted.
The likely cause is the extra throughput: the system now completes 24% more transactions per second, and completion holds row locks while decrementing stock, so more of them queue on the same popular items.
That is a hypothesis rather than something measured.
Under stress, completion is unchanged (2,726ms against 2,712ms), which fits - at 100 stations it was already saturated, so extra throughput changes nothing.

**The bottleneck did not move.**
Completion is still where the system falls over under load, for the same reason as in previous weeks: it holds a database transaction while decrementing stock row by row.
The pipeline was never going to help with that, and did not.

**Nothing was lost to the asynchrony.**
Scan logging is now deferred, which introduced a real risk: the load client fetches the ranking immediately after the last scan response, with no delay.
In both runs the pipeline reported zero dropped scans, and the ranking it published put SKU-000001 first with the same Zipf-shaped tail as the previous weeks.

### Correctness

| | negative stock | exact | explained by selling out | unexplained |
|---|---|---|---|---|
| Default | 0 | 2000 | 0 | 0 |
| Stress | 0 | 1999 | 1 | 0 |

The single non-exact SKU under stress is SKU-000001, the most-scanned item, which sold out and floored at zero.
The contract defines no insufficient-stock response, so this is the same expected edge case the previous weeks documented.

**The three stress-run errors** are client-side timeouts, not failures.
The server logged nothing, the database holds 14,993 completed transactions with none left open, and the invariant shows zero unexplained violations.
The client's per-request timeout is 10 seconds and the slowest completion took 8.93 seconds, so three requests were abandoned on an already-slow tail.

## Requirements

- Docker (for Postgres)
- [`uv`](https://docs.astral.sh/uv/)
- JDK 21+, only to build and run the load client

## Setup

Self-contained: its own Postgres, nothing shared with the other weeks.

```bash
docker compose up -d        # Postgres on host port 5435
uv sync
cp .env.example .env
./scripts/reset_db.sh       # 2000 SKUs, 10,000 units each
```

Port 5435 because 5433 is week 1's and 5434 is week 2's, so all three can run at once.

## Running the server

```bash
uv run python -m app.serve
```

Use this launcher rather than starting `hypercorn` directly.
The Java load client asks to switch to a newer version of HTTP on every request, and accepting makes the server collapse under sustained load.
`app/serve.py` refuses the switch.
Week 1's README documents the full investigation.

## Tests

```bash
uv run pytest
```

Nine tests covering the checkout rules and the pipeline, with no database and no web server, in about a tenth of a second.
Three are new this week: that scans are written in batches rather than one at a time, that a ranking is rebuilt only when due, and that a full queue drops scans rather than failing a customer's checkout.

**There is no linter configured for this project**, so "run the linter" is not something that can be done here.

## Reproducing the load tests

Build the client once. This machine's default Java is 17 and the client needs 21+:

```bash
cd ../load-client
export JAVA_HOME=/Library/Java/JavaVirtualMachines/temurin-21.jdk/Contents/Home
export PATH="$JAVA_HOME/bin:$PATH"
./build.sh
```

Stop the other weeks' containers first, so the databases are not sharing CPU:

```bash
docker stop self-checkout-db self-checkout-db-layered
```

Reset the database and restart the server before **each** run, or the two are not comparable.

```bash
# default
cd ../pipeline && ./scripts/reset_db.sh && uv run python -m app.serve
cd ../load-client
./run.sh --baseUrl=http://localhost:8080 --stations=10 --duration=60 --reportDir=../pipeline/reports

# stress
cd ../pipeline && ./scripts/reset_db.sh && uv run python -m app.serve
cd ../load-client
./run.sh --baseUrl=http://localhost:8080 --stations=100 --duration=120 --reportDir=../pipeline/reports
```

### Checking correctness after a run

```bash
docker exec self-checkout-db-pipeline psql -U checkout -d self_checkout -c "
WITH li AS (
  SELECT ti.sku, COUNT(*) AS completed_units
  FROM transaction_items ti
  JOIN transactions t ON t.transaction_id = ti.transaction_id
  WHERE t.status = 'COMPLETED'
  GROUP BY ti.sku
)
SELECT
  count(*) FILTER (WHERE s.qty < 0)                                              AS negative_stock_skus,
  count(*) FILTER (WHERE (10000 - s.qty) = COALESCE(li.completed_units, 0))      AS invariant_exact,
  count(*) FILTER (WHERE (10000 - s.qty) <> COALESCE(li.completed_units, 0)
                     AND s.qty = 0
                     AND COALESCE(li.completed_units, 0) > 10000)                AS explained_by_selling_out,
  count(*) FILTER (WHERE (10000 - s.qty) <> COALESCE(li.completed_units, 0)
                     AND NOT (s.qty = 0 AND COALESCE(li.completed_units, 0) > 10000)) AS unexplained_violations
FROM stock s LEFT JOIN li ON li.sku = s.sku;
"
```

`unexplained_violations` must be zero.

## Design notes

**Batch size is capped at 200**, below the schema's rebuild interval of 500.
A batch spanning more than one rebuild point would trigger a single rebuild instead of two, leaving the ranking refreshing less often than the schema says it should.

**The queue holding incoming scans is bounded** at 50,000, so a stalled pipeline cannot consume all memory.
If it ever fills, scans are counted and dropped rather than blocking.
Losing a scan costs the ranking a little accuracy; blocking costs a customer their checkout.
Neither run dropped any.

**Shutdown drains the pipeline** before closing the database, so queued scans are not lost on restart.
Draining waits for each filter to confirm it has finished an item, not merely for the queues to look empty - an item can be out of a queue and still being worked on.
Getting this wrong initially cost a published ranking in testing.

**A filter that raises is logged and restarted.**
Without that, one bad batch would silently kill a filter for the life of the process, and scans would pile up with nothing in the logs to explain why.

## Project layout

```
pipeline/
├── app/
│   ├── main.py                    # builds everything, starts and stops the filters
│   ├── serve.py                   # launcher, refuses the HTTP version switch
│   ├── config.py                  # settings from .env
│   ├── errors.py                  # domain errors, no HTTP
│   ├── schemas.py                 # request/response shapes
│   ├── api/                       # routing and error mapping
│   ├── transactions/service.py    # checkout rules
│   ├── analytics/
│   │   ├── pipeline.py            # <- the three filters and their pipes
│   │   └── service.py             # reads the ranking; hands scans to the pipeline
│   └── data/                      # every SQL statement
├── db/{schema.sql, seed.py}
├── scripts/reset_db.sh
├── tests/test_services.py
├── reports/                       # the two submitted runs
├── docker-compose.yml             # Postgres on 5435
└── pyproject.toml
```

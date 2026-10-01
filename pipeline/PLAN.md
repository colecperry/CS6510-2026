# HW4: Pipes and Filters for the Windowed Analytics (Python/FastAPI/Postgres)

> The build plan this implementation was written against, kept as a record of
> how it was put together and what went wrong along the way. Steps are tagged
> `[DONE]` with what actually happened, including the drain bug found at
> Step 11. For the finished result see [README.md](README.md) (setup, the
> filter description, how to reproduce the runs) and
> [QUALITY-ATTRIBUTES.md](QUALITY-ATTRIBUTES.md) (analysis).

## Context

Week 4 of a semester-long project: the same self-checkout API contract
(`spec/self-checkout-openapi.yaml`) gets re-implemented under a different
architecture style each week, always tested by the same unmodified Java load
client, so week-to-week differences reflect the architecture rather than the
test tool.

The assignment states that the application is not generally suitable for a
full pipeline refactor, and that is right. Checkout is request and response:
a customer scans, and the till must answer immediately with a running total.
There is nothing to pipeline there.

The windowed analytics is different. Every scan feeds a stream, the stream is
cut into windows, and each window produces a ranking. That is a pipeline
described in words, and in week 2 it was crammed into one synchronous
function on the request path:

```python
seq = await self._analytics.log_scan(sku)            # round trip 1
window = await self._analytics.claim_recompute(seq)  # round trip 2
if window is None:
    return                                           # 499 times out of 500
```

Both round trips happen while the customer's request is held open, and
neither affects the response.

### Decisions taken before writing code

**Start from `layered/`, refactor rather than rewrite.** The analytics write
path was already isolated in two files with exactly one caller, so this
touches one method's internals and leaves the four layers intact. Starting
from the monolith would have meant separating concerns and pipelining at the
same time.

**Three filters.** They map onto the three things the existing code already
did, and onto the shape of the example in the assignment brief. Two would
work - the window filter is only four lines - but ingest is the only filter
under real pressure, and keeping its loop to a single database round trip
matters more than saving a file.

**Pipes are `asyncio.Queue`, not threads.** The whole application is already
`asyncio` and `asyncpg` connections are not thread-safe, so threads would
have meant either a second connection pool or cross-thread hand-offs, for no
benefit.

**The ingest filter batches its writes.** This is the concrete payoff and it
is about five extra lines.

**Self-contained, including its own database.** `pipeline/` copies
`docker-compose.yml`, `db/`, and `scripts/` rather than borrowing week 2's.
The container is `self-checkout-db-pipeline` on host port **5435**, so all
three weeks can exist at once.

Each step below is one file or one command. We implement one step per turn:
explain what the step does and why, do only that step, then explain what
changed before moving on.

---

## A. Scaffold

## Step 1 - Copy `layered/` to `pipeline/` [DONE]

Copied with `rsync`, excluding `.venv/`, `__pycache__/`, `reports/`, the
three markdown files, and `uv.lock`. Verified with `diff -r` that every
copied file was byte-identical. A stray `.pytest_cache/` came along and was
removed, and added to `.gitignore` - worth noting that `layered/.gitignore`
is missing that line too, left alone as out of scope.
Depends on: nothing.

## Step 2 - `pyproject.toml` [DONE]

Renamed the project to `pipeline`. Dependencies untouched, including the
exact `starlette==1.6.0` pin from week 2 - Starlette sits on every request,
so a version drift would show up as a latency difference having nothing to do
with the architecture.
Depends on: Step 1.

## Step 3 - `docker-compose.yml` [DONE]

`container_name: self-checkout-db-pipeline` and host port `5435`. Both
changes were necessary rather than cosmetic: container names and host ports
are global to the Docker daemon, so reusing another week's would make
whichever started second fail.
Depends on: nothing.

## Step 4 - `.env.example`, `.env`, and a default I nearly missed [DONE]

Pointed both at 5435. **Also found a third place the port appears**:
`app/config.py` holds a fallback used when `.env` is absent, and it still
said 5434. That would not have failed - it would have silently connected to
week 2's database and written this week's scans into it. A quiet wrong answer
is worse than a crash.
Depends on: Step 3.

## Step 5 - `uv sync` [DONE]

Built the environment. Verified afterwards that the dependency list matches
`layered/` exactly.
Depends on: Step 2.

## Step 6 - Container up, database seeded [DONE]

Confirmed the copied folder stands up on its own before any code changed:
2,000 catalog rows, 10,000 units each, window 1000/500, scan log empty.
Identical starting conditions to the previous weeks, which is what makes the
benchmark comparison meaningful.
Depends on: Steps 3, 5.

---

## B. Database access

## Step 7 - `log_scan` becomes `log_scans` [DONE]

One row at a time became a whole batch in one statement:

```sql
WITH inserted AS (
  INSERT INTO scan_log (sku) SELECT unnest($1::text[]) RETURNING seq
)
SELECT max(seq) FROM inserted
```

`unnest()` turns a list into rows, so 200 scans cost one round trip rather
than 200. The `max(seq)` wrapper matters: without it, `fetchval` returns the
**first** sequence number rather than the highest, which would make windows
fire late. Verified against the real database that a batch of 5 returns the
highest of the 5.
Depends on: nothing.

---

## C. The filters

## Step 8 - `pipeline.py`: the class, the three queues, `submit()` [DONE]

Measured the thing the whole week rests on: `submit()` takes **0.23
microseconds**, against roughly 1,000 for the database write it replaces.

Two decisions recorded in the file. `MAX_BATCH = 200`, below the schema's
rebuild interval of 500, so one batch can never skip a rebuild point.
`MAX_PENDING = 50,000` on the incoming queue, with a full queue dropping and
counting rather than raising - an exception here would break a sale because a
statistics feature is behind.
Depends on: nothing.

## Step 9 - The ingest filter [DONE]

Verified against the real database: 1,000 scans produced **5 database writes**
of 200 each, with all 1,000 rows stored and the queue drained.

The loop adapts on its own because the two `get` calls differ - the first
waits for a scan, the second takes only what is already queued. So the batch
size is whatever the traffic happens to be, with no timer and nothing to
tune.
Depends on: Steps 7, 8.

## Step 10 - The window and ranker filters [DONE]

Ran all three together against Zipf-weighted scans. At 400 scans: no ranking,
correctly, because the threshold is 500. At 800: a ranking covering scans 0
to 600 with SKU-000001 on top and a clean descending tail.
Depends on: Step 9.

## Step 11 - `start()`, `stop()`, and a real bug [DONE]

**The first `stop()` was wrong.** It waited for the three queues to be
*empty*, which seemed obviously right. The test caught it: 600 scans written
to the database but the ranking never published.

An item can be out of a queue while a filter is still working on it. All
three queues read empty, drain declared victory, and `stop()` cancelled the
ranker mid-publish.

Fixed with the mechanism asyncio provides for exactly this. Each filter calls
`task_done()` after it has passed work downstream, and drain uses `join()`,
which waits for those acknowledgements rather than for the queue to look
empty. This would have surfaced in production as "the ranking is sometimes
stale after a restart".

Also added a guard so a filter that raises is logged and restarted. Tested by
making ingest throw on its first run: it logged, restarted, and the next scan
was written normally. Without it, one bad batch would silently kill a filter
for the life of the process.
Depends on: Step 10.

---

## D. Wiring

## Step 12 - `AnalyticsService.record_scan` delegates to the pipeline [DONE]

A 25-line method became one line. The constructor also shed two dependencies
- the catalog and the snapshot cap both belonged to the ranking-building work
and moved with it. That is the useful signal: when work moves and the old
class keeps all its dependencies, the split is probably not clean.
Depends on: Step 11.

## Step 13 - Drop the `await` at the one call site [DONE]

One word removed in `transactions/service.py`. That word is where the latency
improvement actually lands; everything else this week exists to make removing
it safe. The `await` on the line above it, writing the basket line item,
stays - that must be finished before the customer is told the scan worked.
Depends on: Step 12.

## Step 14 - `main.py` builds and runs the pipeline [DONE]

Creates the pipeline, starts the three filters at startup, stops them before
closing the pool. The order matters: closing the pool first would leave the
filters writing to a database that is gone.

First point at which the app ran. Verified through real HTTP: 600 scans
produced a published ranking with SKU-000001 first at 165 scans.
Depends on: Step 13.

---

## E. Tests

## Step 15 - Update the tests [DONE]

Two failed as predicted: the fake analytics service was still `async`, and
the rebuild test called `AnalyticsService` with arguments that no longer
exist.

Replaced with three tests against the pipeline: that scans are written in
batches rather than one at a time, that a rebuild happens only when due, and
that a full queue drops scans rather than failing a checkout. Nine tests,
0.09 seconds, no database.

**Checked the batching test is not vacuous** by forcing `MAX_BATCH = 1` and
confirming it fails. A passing test that cannot fail is worse than no test.

One wart recorded rather than hidden: the rebuild test needs
`await asyncio.sleep(0)` between submissions, because otherwise batching does
its job and all five scans arrive as one write with one sequence number. That
is a test accommodating the design rather than testing it naturally.
Depends on: Steps 12, 14.

---

## F. Verification and benchmarking

## Step 17 - Smoke test [DONE]

All seven endpoints returned correct shapes. All six error paths returned the
right codes: unknown SKU 404, closed basket 409, missing basket 404, double
payment 409, blank station 400, empty basket 409.
Depends on: Step 14.

## Step 18 - Default run: 10 stations, 60s [DONE]

7,426 transactions (123.6/sec), 78,167 items (1,300.9/sec), 0.00% errors.
**SCAN_ITEM p95 7.10ms against week 2's 9.49ms.** Invariant: all 2,000 SKUs
exact, zero unexplained.
Depends on: Step 17.

## Step 19 - Stress run: 100 stations, 120s [DONE]

14,990 transactions (124.5/sec), 158,157 items (1,313.7/sec).
**SCAN_ITEM p95 10.88ms against week 2's 19.37ms**, a 44% improvement.
Zero scans dropped by the pipeline, no server errors.

Three completion errors, as in week 2, and the same investigation: the server
logged nothing, the database holds 14,993 completed transactions with none
left open, and the invariant is clean. Client-side timeouts on an
already-slow tail, not failures.
Depends on: Step 18.

## Step 20 - Correctness and cross-week comparison [DONE]

Default: 0 negative, 2,000 exact, 0 unexplained.
Stress: 0 negative, 1,999 exact, 1 explained by selling out, 0 unexplained.

Popular items ranked SKU-000001 first in all three weeks with the same
Zipf-shaped tail, which is the cross-week check the course README asks for.

**One regression found and not explained away.** COMPLETE_TRANSACTION p95 at
default load went from 20.95ms to 31.95ms. The likely cause is the 24%
throughput gain putting more completions per second onto the same row locks,
but that is a hypothesis and is labelled as one in both write-ups. Confirming
it would mean rate-limiting the pipeline version to the old throughput and
re-measuring.
Depends on: Step 19.

---

## G. Documentation and submission

## Step 21 - `README.md` [DONE]

Setup, the filter description for the written deliverable, reproduction
commands, the invariant query, and a three-way results table. Every figure
was checked against the report JSONs with a script rather than quoted from
memory.
Depends on: Step 20.

## Step 22 - `QUALITY-ATTRIBUTES.md` and this file [DONE]

Written after the numbers existed.
Depends on: Step 20.

## Step 23 - Commit and push [DONE]

Depends on: Step 22.

---

## Verification (referenced by Steps 18-20)

The four-count invariant query from week 1's README, which separates a
genuine violation from the expected sold-out case. Run after each load test:

```sql
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
```

`unexplained_violations` must be zero.

**Additional check this week:** the pipeline reports any scans it dropped, at
shutdown. Both runs reported none. Because scan logging is now asynchronous,
a dropped scan would silently undercount the ranking rather than failing a
request, so this needs checking explicitly.

## Benchmark protocol

1. `docker stop self-checkout-db self-checkout-db-layered` - the other weeks'
   databases must not share CPU with this one during a timed run.
2. Confirm nothing is holding port 8080. Week 1's plan records zombie server
   processes as a real source of skewed runs.
3. `./scripts/reset_db.sh`
4. Start the server with `uv run python -m app.serve`.
5. Run the client with `--reportDir=../pipeline/reports`.
6. Run the invariant query, and check the server log for dropped scans.
7. Repeat from 3 for the second parameter set.

## What did not change, deliberately

Listed because the value of the comparison rests on it:

- The API contract, and every response shape.
- `db/schema.sql`, byte-identical.
- The seed data: 2,000 SKUs, 10,000 units, same price formula.
- The counting, windowing and pruning SQL. Only the insert changed, and only
  to accept a list instead of one value.
- The checkout path: `transactions/service.py` is unchanged apart from one
  removed `await`.
- `serve.py` and its HTTP-version patch, byte-identical.
- Pool sizing, and Postgres `max_connections`.
- All dependency versions, including the pinned `starlette==1.6.0`.
- Load parameters: 10/60 and 100/120, matching the previous weeks rather than
  the course README's suggested 200/180.

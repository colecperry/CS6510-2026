# HW1: Monolithic Self-Checkout Implementation (Python/FastAPI/Postgres)

> The build plan this implementation was written against, kept as a record of
> how it was put together and what went wrong along the way. Steps are tagged
> `[DONE]` with what actually happened, including the debugging in Steps 23-24.
> For the finished result see [README.md](README.md) (setup, how to reproduce
> the runs) and [QUALITY-ATTRIBUTES.md](QUALITY-ATTRIBUTES.md) (analysis).

## Context

This is week 1 of a semester-long project: the same self-checkout API contract
(`spec/self-checkout-openapi.yaml`) gets re-implemented under a different
architecture style each week (monolith → layered → service-based →
microservices → event-driven), always tested by the same unmodified Java load
client, so week-to-week differences reflect the architecture, not the test
tool.

This week's task: build a **monolithic** implementation using **Python +
FastAPI + PostgreSQL with raw SQL** (asyncpg, no ORM) in a new `monolith/`
folder inside the existing cloned repo
(`homeworks/hw_1/CS6510-2026/`), alongside `spec/`, `load-client/`,
`mockserver/`. Chosen deliberately: FastAPI's async support matches the load
client's concurrent-station traffic, and raw SQL is direct practice for the
user's job starting January (primarily Python + SQL) - including seeding the
database from a Python script rather than a plain `.sql` file, for the same
reason.

The central technical challenge is the **concurrency-safe stock decrement**:
with up to 100 stations completing transactions concurrently against shared
inventory rows, a naive read-then-write can let two stations both buy the
last unit of an item. Grading invariant: for every SKU, `initial_stock -
final_stock` must equal total completed-transaction line items for that SKU,
and stock must never go negative.

The second non-trivial piece is a **true hopping-window** popular-items
feature (recompute every `slideInterval` scans, not every scan, not a full
table scan) - `mockserver/MockServer.java:267-273` explicitly does *not*
implement this correctly and leaves it as the exercise.

Docker, Python 3, and an authenticated GitHub CLI (`gh`, account
`colecperry`) are already available locally. The project's Postgres runs in
Docker on host port **5433** (not the default 5432), because a native
Homebrew Postgres 14 is already running on this machine for other projects -
the two never interact.

Each step below is one file, one script, or one focused piece of logic. We
implement one step per turn: I'll explain what the step does and why before
writing it, do only that step, then explain what changed before we move on.

---

## Step 1 - Fork the repo and repoint the remote [DONE]

Forked `gortonator/CS6510-2026` to `colecperry`'s GitHub account. `origin`
now points at `https://github.com/colecperry/CS6510-2026.git` (HTTPS, not
SSH - this machine's SSH config only has key aliases for `github-personal`/
`github-northeastern`, not plain `github.com`, so HTTPS avoids that entirely).
`upstream` points at the original instructor repo. Push access confirmed.

## Step 2 - `docker-compose.yml` for Postgres [DONE]

Created `monolith/docker-compose.yml`: one Postgres 16 service, user/password/
db `checkout`/`checkout`/`self_checkout`, host port **5433** mapped to the
container's 5432 (see Context above for why), named volume `pgdata` for
`docker compose down -v` to give a full clean slate if ever needed.
Depends on: nothing.

## Step 3 - `db/schema.sql` [DONE]

Created `monolith/db/schema.sql`: DROP+CREATE (idempotent full rebuild) for
`catalog`, `stock`, `transactions`, `transaction_items`, `scan_log`,
`popularity_state`, `popularity_snapshot`, plus the `tx_seq`/`scan_seq`
sequences. `transactions` (one row per checkout) and `transaction_items` (one
row per scanned unit) are separate tables - classic order/order-line-items
pattern, needed for itemized receipts and per-SKU stock decrements.
`scan_log` is a separate prunable stream from `transaction_items` so popularity
cleanup can never affect an in-flight basket. Not yet executed against a real
database - first real run will be Step 7.
Depends on: nothing.

## Step 4 - Dependencies via `uv` and `.env.example` [DONE]

Used `uv init --bare` to create `pyproject.toml` (not `requirements.txt` -
`uv` is this machine's standard Python package manager per global config),
pinned `.python-version` to **3.12** (the system default `python3` is 3.14,
too new for guaranteed `asyncpg` wheel support), and `uv add`ed fastapi,
uvicorn[standard], asyncpg, pydantic, pydantic-settings - `uv.lock` now
pins all 21 resolved packages exactly. Also created `monolith/.env.example`
(`DATABASE_URL=postgresql://checkout:checkout@localhost:5433/self_checkout`,
`WINDOW_SIZE=1000`, `SLIDE_INTERVAL=500`, `LOW_STOCK_DEFAULT_THRESHOLD=50`,
`POPULARITY_SNAPSHOT_CAP=100`), a local `.env` copy, and `.gitignore`
(`.venv/`, `.env`, `__pycache__/`) since none existed in this repo yet.
Depends on: Step 2 (for the exact port/credentials in `DATABASE_URL`).

## Step 5 - `app/config.py` [DONE]

Created a `pydantic-settings` `Settings` class that auto-reads `.env` into
typed values (str/int), with defaults matching `.env.example`. Every later
module imports the single shared `settings` instance instead of reading
`os.environ` directly. Verified: `uv run python -c "from app.config import
settings; print(settings)"` printed all 5 values correctly.
Depends on: Step 4.

## Step 6 - `app/pool.py` [DONE]

Create the asyncpg connection pool factory (`connect()`/`disconnect()`) and
a FastAPI dependency (`get_conn()`). Named `pool.py`, not `db.py` - it only
manages the connection pool itself, no queries live here, and a top-level
`db/` folder already exists for schema/seed files, so `db.py` would have
been a confusing near-duplicate name. This is shared: the seed script (Step
7) and every router we build later all get their database connections
through this one file.
Depends on: Step 5.

## Step 7 - `db/seed.py` (Python, not a plain `.sql` file) [DONE]

A Python script that: reads and executes `db/schema.sql`'s DDL text (drop +
recreate every table), then computes the 2000-SKU catalog *in Python*
(SKU/name/price, matching `MockServer.java`'s formula) and bulk-loads it plus
starting stock (10,000/SKU) using `asyncpg`'s `copy_records_to_table` (the
Postgres `COPY` protocol - the standard, fast way to bulk-insert from Python,
worth knowing for the January job). This replaces the plain-SQL
`db/seed.sql` originally planned, so the seeding logic is genuine Python +
SQL practice rather than a `.sql` file invoked by a shell script.
Depends on: Step 3 (schema.sql content), Step 6 (connection helper in `app/pool.py`).
Note: running a script directly (`python db/seed.py`) doesn't put the
project root on the import path, so `import app.pool` fails - fixed by
adding `db/__init__.py` and running it as a module (`python -m db.seed`)
instead.

## Step 8 - `scripts/reset_db.sh` [DONE]

A thin wrapper: `cd`s to `monolith/` (so it works no matter what directory
it's called from) then runs `uv run python -m db.seed`. This is the
"reinitialize between test runs" button required by the assignment - one
command, no `psql` CLI dependency needed since everything goes through
`asyncpg` now. Verified by running it from the repo root (not from
`monolith/`) and confirming it still re-seeds correctly.
Depends on: Step 7.

## Step 9 - `app/catalog_cache.py` [DONE]

Create the in-memory `sku -> (name, price)` dict, populated once at startup
from the `catalog` table, so `GET /items` and SKU lookups during scanning
never hit the DB per request.
Depends on: Step 6 (needs a connection to load from).

## Step 10 - `app/models.py` [DONE]

Create Pydantic models mirroring every OpenAPI schema 1:1 (CatalogItem,
Transaction, ScanResult, Receipt, ReceiptLine, LowStockAlert/Response,
PopularItem/Response, ApiError). One focused file, no logic.
Depends on: nothing (can happen any time before Step 12).

## Step 11 - `app/errors.py` [DONE]

Create `NotFoundError` / `ConflictError` / `InvalidRequestError` exception
classes and their FastAPI exception handlers, mapping to 404/409/400 JSON
matching the `ApiError` schema.
Depends on: Step 10 (uses the ApiError model).

## Step 12 - `app/main.py` [DONE]

Wire the FastAPI app: lifespan startup (create the pool from Step 6, load
the cache from Step 9), register the exception handlers from Step 11, and
include routers (empty router files created as stubs here; filled in over
the next steps).
Depends on: Steps 5, 6, 9, 10, 11.

## Step 13 - `GET /items` (`app/routers/catalog.py`) [DONE]

Simplest endpoint: serve the catalog from the in-memory cache. First
end-to-end slice we can actually run and curl.
Depends on: Step 12.

## Step 14 - `POST /transactions` (`app/routers/transactions.py`) [DONE]

Validate `stationId`, insert a new row, return the `Transaction` shape.
No concurrency concerns (each row is new).
Depends on: Step 13 (router file/pattern established).

## Step 15 - `POST /transactions/{id}/items` (scan, no popularity yet) [DONE]

Look up SKU in the cache (404 if unknown), atomically update the
transaction's running counters (409 if not OPEN), insert into
`transaction_items`. Deliberately *excludes* the `scan_log`/popularity
writes - that's Step 19, kept separate so this step is just "record the
scan."
Depends on: Step 14.

## Step 16 - `POST /transactions/{id}/complete` [DONE]

Verified with real concurrency (asyncio.gather, not sequential): 50
concurrent completions against 1000 units of stock left exactly 950 (no
lost updates); 20 concurrent completions against 5 units of stock all
succeeded and stock floored at 0, never negative (documented edge case,
not a bug). Also confirmed the Step 15 409 path (scan after complete) now
works correctly.

The concurrency-critical step. Atomically flip status to COMPLETED (folding
the empty-basket 409 check into the same query), then aggregate
`transaction_items` per SKU and issue one atomic
`UPDATE stock SET qty = GREATEST(qty - $2, 0) WHERE sku = $1` per SKU,
processed in `ORDER BY sku` to keep lock order consistent across concurrent
completions. Build the `Receipt` from the same aggregation.
Depends on: Step 15 (needs baskets to exist).

## Step 17 - `GET /transactions/{id}` [DONE]

Plain status lookup, not performance-critical, mostly for debugging while we
build.
Depends on: Step 14.

## Step 18 - `GET /inventory/low-stock` (`app/routers/inventory.py`) [DONE]

Query `stock` joined to `catalog` for rows under the threshold (query param
or env default).
Depends on: Step 16 (so there's stock movement to observe).

## Step 19 - Popular-items write path (extend Step 15's scan endpoint) [DONE]

Verified with a real 1500-scan sequence across 3 SKUs: window correctly
hopped from [0,1000] to [500,1500], per-SKU counts within the new window
matched hand-computed expectations exactly, and scan_log pruned from 1200
down to exactly 1000 rows (bounded, not growing unboundedly). Confirms
this implements true hopping-window semantics, unlike the mock server.

Add the `scan_log` insert and the hopping-window recompute-claim logic to
the scan endpoint: insert into `scan_log`, then attempt the atomic
conditional `UPDATE popularity_state ... WHERE $1 - window_end >=
slide_interval`; if it claims the recompute, aggregate the window into
`popularity_snapshot` and prune `scan_log` rows behind `window_start`. This
is its own step (not part of Step 15) because it's a distinct concept - the
hopping window - layered on top of already-working scanning.
Depends on: Step 15, Step 3 (scan_log/popularity tables).

## Step 20 - `GET /analytics/popular-items` (`app/routers/analytics.py`) [DONE]

Verified against real data left over from Step 19's test: response exactly
matched the hand-verified window [500,1500] and per-SKU ranking. All 7
spec endpoints are now implemented.

Two cheap reads: `popularity_state` for the window metadata,
`popularity_snapshot` for the ranked items.
Depends on: Step 19.

## Step 21 - Manual smoke test [DONE]

Verified all 7 endpoints plus 3 error cases via real curl/HTTP against a
freshly-started server (not the in-process TestClient used throughout
development) - every response matched the spec's shape exactly. Found and
cleaned up a stray uvicorn process left over from earlier manual-testing
demo, holding port 8080.

Run the server, `curl` every endpoint by hand, confirm response shapes match
the OpenAPI schemas exactly (field names/types) before bringing in the load
client.
Depends on: Steps 13-20 all complete.

## Step 22 - Build the load client [DONE]

Found two environment issues: build.sh/run.sh weren't executable (fixed
with chmod +x), and this machine's default Java is 17 via SDKMAN but the
client requires 21 (virtual threads). A JDK 21 was already installed at
/Library/Java/JavaVirtualMachines/temurin-21.jdk - must export JAVA_HOME
to that path (and prepend its bin/ to PATH) every time the load client is
built or run in later steps. Verified `--help` runs correctly under 21.

`cd load-client && ./build.sh` - unmodified, provided code, just needs
compiling once.
Depends on: nothing (independent of the server).

## Step 23 - Default load-test run + correctness check [DONE]

Found and fixed two real bugs the load client exposed that curl/TestClient
testing never would have:
1. Java's HttpClient probes every request with `Upgrade: h2c`. Both of
   uvicorn's HTTP implementations (h11, httptools) mishandled this -
   confirmed via raw TCP capture that the request body was sent correctly
   on the wire, so the bug was 100% server-side. Fixed by switching from
   uvicorn to hypercorn (real HTTP/2 support). uvicorn removed from
   dependencies entirely; README updated to explain why.
2. asyncpg's default connection pool max_size (10) exactly matched our
   10-station test with zero headroom for the popularity recompute path's
   extra connection, causing some requests to queue past the client's
   10s timeout. Fixed by setting min_size=10, max_size=120 in app/pool.py
   (sized for stress mode's up to-100 stations plus headroom).

This run was later redone (see Step 24) so both submitted reports come
from identical server code. Final default-params result:
**5,905 transactions, 62,027 items scanned, 0.00% errors** on all three
operations. Invariant: all 2,000 SKUs exact, zero violations, zero
negative stock. Report: monolith/reports/report-20260918-193127.json.

`./scripts/reset_db.sh`, start the server, run
`./run.sh --baseUrl=http://localhost:8080 --stations=10 --duration=60
--reportDir=../monolith/reports`, then run the invariant-check SQL query
(every SKU's `10000 - qty` must equal its completed line items, never
negative). Save the resulting report JSON.
Depends on: Step 21, Step 22.

## Step 24 - Stress load-test run + correctness check [DONE]

Stress mode exposed three more issues beyond Step 23's, each fixed:
1. Postgres `max_connections` defaulted to 100, below our pool's max_size
   of 120. Raised to 200 via a `command:` override in docker-compose.yml.
2. Zombie server processes accumulated from rapid restarts (`kill` without
   verification left three alive, each holding its own connection pool).
   This silently skewed several test runs. Lesson: always verify the port
   is clear and exactly one process exists before testing.
3. **Root cause of the mass timeouts:** hypercorn ignores the client's
   `Upgrade: h2c` probe on requests *with* a body, but the load client's
   single bodyless `GET /items` at startup does trigger a real upgrade -
   after which Java's client sends everything over HTTP/2. That
   destabilised badly under sustained load (stream-limit errors, then mass
   timeouts and an eventual hang). Found by reading hypercorn's
   `protocol/h11.py:311` (`if upgrade_value.lower() == "h2c" and not
   has_body`). Fixed in app/serve.py by patching `_check_protocol` to
   refuse the upgrade - no config flag exists for this. Took stress-mode
   errors from widespread timeouts to 0.00%.

Final stress result: **14,085 transactions (117/sec), 147,182 items
scanned (1,222/sec), 0.00% errors** on all three operations. p50/p95/p99
for COMPLETE_TRANSACTION: 293ms / 2,723ms / 4,486ms - the clear pressure
point under stress, worth discussing in Step 25.

Invariant under stress: 0 SKUs negative, 1,999 exact, 1 discrepancy fully
explained by depletion (SKU-000001 had 17,884 units demanded vs 10,000 in
stock, floored at 0 - the edge case predicted at planning time), and
**0 unexplained violations**. Report:
monolith/reports/report-20260918-192926.json.

Reset the DB again, run
`./run.sh --baseUrl=http://localhost:8080 --stations=100 --duration=120
--reportDir=../monolith/reports`, re-run the invariant check, save the
report JSON.
Depends on: Step 23.

## Step 25 - Architectural characteristics analysis [DONE]

Originally scoped as "you write it, I review it", but written up on
request instead: `monolith/QUALITY-ATTRIBUTES.md`. Covers the required
characteristics with concrete requirements, the top three prioritized
(data integrity, scan-path responsiveness, concurrency/scalability) with
explicit trade-offs for each, and an interpretation of the Step 23-24
numbers. Every figure cited was verified against the report JSONs rather
than quoted from memory.
Depends on: Step 24 (needs real report numbers to reference).

## Step 26 - Push to your fork [NOT DONE]

Commit the `monolith/` folder, the two reports, and the analysis markdown;
push to `origin` (your fork) for submission. **Requires your explicit
confirmation before pushing.**
Depends on: Step 25.

---

## Verification (referenced by Steps 21-24)

Correctness invariant query, run after each load-test run:
```sql
SELECT s.sku, 10000 - s.qty AS decremented,
       COALESCE(li.total_units, 0) AS completed_units, s.qty AS final_stock
FROM stock s
LEFT JOIN (
  SELECT ti.sku, COUNT(*) AS total_units
  FROM transaction_items ti
  JOIN transactions t ON t.transaction_id = ti.transaction_id
  WHERE t.status = 'COMPLETED'
  GROUP BY ti.sku
) li ON li.sku = s.sku
WHERE (10000 - s.qty) != COALESCE(li.total_units, 0) OR s.qty < 0;
```
Must return **zero rows**.

**Known edge case to document, not "fix":** the spec has no
insufficient-stock error path ("payment always succeeds"), and flooring at
zero means the invariant holds only as long as no single SKU's cumulative
completed demand exceeds its starting 10,000 units during a run. The load
client's Zipf-skewed sampling makes a hot SKU hitting 0 plausible,
especially in stress mode - treat this as an expected domain edge case in
the write-up if observed, not a decrement-logic bug.

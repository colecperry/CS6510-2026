# HW2: Layered Self-Checkout Implementation (Python/FastAPI/Postgres)

> The build plan this implementation was written against, kept as a record of
> how it was put together and what went wrong along the way. Steps are tagged
> `[DONE]` with what actually happened, including the two real defects found
> in Steps 28 and 33. For the finished result see [README.md](README.md)
> (setup, how to reproduce the runs) and
> [QUALITY-ATTRIBUTES.md](QUALITY-ATTRIBUTES.md) (analysis).

## Context

Week 2 of a semester-long project: the same self-checkout API contract
(`spec/self-checkout-openapi.yaml`) gets re-implemented under a different
architecture style each week (monolith → **layered** → service-based →
microservices → event-driven), always tested by the same unmodified Java load
client, so week-to-week differences reflect the architecture, not the test
tool.

This week's task: restructure week 1's monolith into a layered architecture
with an API layer, a transactions layer, an analytics layer, and a database
access layer, in a new `layered/` folder. `monolith/` is left untouched so
the two can be compared directly.

The starting point was already partly there: week 1 had four FastAPI routers,
so the delivery code was grouped by endpoint. What it did not have was any
separation between delivery, rules, and storage *inside* those routers.
`scan_item` was 55 lines doing a cache lookup, a conditional UPDATE, a second
query to distinguish 404 from 409, a line-item INSERT, an analytics call, and
response assembly.

Because the grading criterion is a week-over-week latency comparison, the
governing constraint was that **nothing except the architecture may change**.
Same contract, same SQL, same database, same starting data, same load client,
same load parameters. Anything else that moved would make the comparison
meaningless.

### Decisions taken before writing code

**`GET /items` joins the transactions layer, `GET /inventory/low-stock` joins
analytics.** The assignment names four layers, but the API has four endpoint
groups: transactions, analytics, catalog, and inventory. Catalog reads go with
transactions because scanning depends on catalog pricing and they are one
concern. Low stock goes with analytics because it is a read-only reporting
view over data the transactions layer writes.

**No parallel domain types.** The stricter version of layering has business
services return their own dataclasses that the API layer maps to wire models.
For this system those types would have had identical fields to the Pydantic
models, so the mapping would have been pure overhead on the hottest path.
Pydantic is not FastAPI, so the services stay unit-testable either way.
`app/schemas.py` sits above all four layers rather than inside `api/`, so a
service importing it is not reaching upward into the delivery layer.

**Services built once at startup, not injected per request.** FastAPI's
`Depends` resolves on every request; at 1,200 scans a second that is real work
for stateless objects that never change. `main.py` constructs everything in
the lifespan and stores it in `app/api/wiring.py`.

**Self-contained, including its own database.** `layered/` copies
`docker-compose.yml`, `db/schema.sql`, `db/seed.py` and `scripts/reset_db.sh`
rather than borrowing week 1's. Sharing was considered and rejected:
`monolith/db/seed.py` begins with `import app.pool`, so borrowing the database
would mean running monolith Python to prepare the layered app. The container
is `self-checkout-db-layered` on host port **5434**, so both weeks can exist
at once.

Each step below is one file or one command. We implement one step per turn:
explain what the step does and why, do only that step, then explain what
changed before moving on.

---

## A. Scaffold

## Step 1 - Create `layered/` and copy unchanged config [DONE]

Copied `.python-version` (3.12) and `.gitignore` from `monolith/`. The local
`.gitignore` matters: the repo-root one only covers `load-client/out/` and
`mockserver/out/`, so without it `.venv` and `.env` would be committed. It
deliberately does *not* ignore `reports/`, which the grading instructions
require to be committed.
Depends on: nothing.

## Step 2 - `pyproject.toml` [DONE]

Same five runtime dependencies as week 1 at identical version floors, so a
latency difference cannot come from a library upgrade. Dev group is `pytest`
and `pytest-asyncio` only - week 1's `httpx2` was dropped because the tests
here drive service classes directly and make no HTTP requests. Set
`asyncio_mode = "auto"`, since without it pytest silently skips async tests
and reports a green run that tested nothing.
Depends on: Step 1.

## Step 3 - `docker-compose.yml` [DONE]

Week 1's file with two changes: `container_name: self-checkout-db-layered` and
host port `5434`. Both were necessary rather than cosmetic - container names
and host ports are global to the Docker daemon, so reusing week 1's would make
whichever stack started second fail. `max_connections=200` kept verbatim.
Depends on: nothing.

## Step 4 - `.env.example` and `.env` [DONE]

Port 5434, and `WINDOW_SIZE` / `SLIDE_INTERVAL` dropped. Those two were dead
config in week 1: `config.py` loaded them but nothing read them, because the
real values are column defaults on `popularity_state` in `schema.sql` and the
analytics code reads them back from that row. Removing them was not optional -
`pydantic-settings` rejects unknown keys in a `.env` file, so once Step 8
removed the fields, leaving them here would crash the app at startup.
Depends on: Step 3.

## Step 5 - `uv sync` [DONE]

Built the environment, 26 packages. **Found a real problem:** all five direct
dependencies matched week 1 exactly, but `starlette` resolved to 1.7.0 against
week 1's 1.6.0. Starlette is the ASGI layer underneath FastAPI and sits on
every request, so a version drift there would show up as a latency difference
having nothing to do with the architecture. Pinned `starlette==1.6.0` exactly
and re-synced. Verified afterwards that all 18 runtime packages present in both
lockfiles now match.
Depends on: Step 2.

## Step 6 - Copy `db/schema.sql` [DONE]

Byte-identical, confirmed with `diff`. The schema defines the starting
conditions of every benchmark - 2,000 SKUs, 10,000 units each, the
`qty >= 0` constraint, the window defaults - so any drift would mean the two
weeks measured different systems. Also copied the empty `db/__init__.py`.
Depends on: nothing.

## Step 7 - Copy `scripts/reset_db.sh` [DONE]

Byte-identical, with `cp -p` to preserve the executable bit. Needed no edits
because it navigates by relative path (`cd "$(dirname "$0")/.."`), so it
resolves to whichever project contains it.
Depends on: nothing.

---

## B. Shared kernel

## Step 8 - `app/config.py` [DONE]

Week 1's settings minus the two dead fields (see Step 4), with a comment
recording where the popularity window values actually live so the next reader
does not have to work out whether their absence was deliberate.
Depends on: Step 4.

## Step 9 - `app/errors.py` [DONE]

`DomainError` plus `NotFoundError`, `ConflictError`, `InvalidRequestError`,
with **no `status_code` attribute and no FastAPI import**. This is the change
that decouples the business layers: in week 1 `ConflictError` carried
`status_code = 409`, so checkout logic knew how its failures would be
rendered. Added `super().__init__(message)`, which week 1 lacked, so an
unhandled error prints its message rather than a bare class name.
Depends on: nothing.

## Step 10 - `app/schemas.py` [DONE]

Week 1's `models.py`, copied verbatim. It was already pure - no logic, no
database, no FastAPI - so there was nothing to fix. Renamed because "models"
usually means database tables, and this project now has a data layer where
that would mislead. Verified camelCase serialisation survived the move.
Depends on: nothing.

---

## C. Database access layer

## Step 11 - `app/data/pool.py` [DONE]

`create_pool()` / `close_pool()`. The change from week 1 is that the pool is
**returned rather than stored in a module global**. With a global, any file
could run SQL just by importing the module, so the layer boundary would rest
on everyone remembering the rule. Now a router has no way to obtain a
connection. Week 1's unused `get_conn()` dependency was dropped. Pool sizing
`min_size=10, max_size=120` kept verbatim.
Depends on: Step 8.

## Step 12 - `db/seed.py` [DONE]

Copy with the import repointed at `app.data.pool`, plus a `try/finally` so a
failed schema load does not leave connections open. Brought the container up
and seeded for real: 2,000 catalog rows, 20,000,000 total units, every SKU at
10,000, window 1000/500, prices matching week 1 (`SKU-000001 @ 0.85`,
`SKU-002000 @ 9.60`). Volume created as `layered_pgdata`, confirming Compose
namespaces volumes per project and week 1's data is untouched.
Depends on: Steps 6, 11.

## Step 13 - `app/data/catalog_repo.py` [DONE]

The in-memory catalog cache, now a class rather than a module global dict.
`get()` is deliberately **not** `async` - it is a dictionary read, and marking
it async would build a coroutine on every scan. Used a `NamedTuple` for the
return so callers read `entry.price` rather than `entry[2]`; it is still a
tuple underneath. The `Decimal -> float` cast now happens once here instead of
at the eight call sites week 1 had. Verified against the seeded database.
Depends on: Step 11.

## Step 14 - `app/data/transactions_repo.py`, part 1 [DONE]

`start`, `bump_counters`, `state_of`, `add_line`, `fetch`. The design point is
the split between `bump_counters` and `state_of`: the former returns `None`
for both "no such basket" and "basket closed", so `state_of` exists purely to
tell those apart, and only runs after a write was already refused. Verified
all five against the database, including that a missing basket gives
`state_of() -> None` while a closed one gives `TransactionState('COMPLETED', 2)`.
Depends on: Step 11.

## Step 15 - `app/data/transactions_repo.py`, part 2: `complete()` [DONE]

The payment path, held in one method so the all-or-nothing guarantee is a
property of one readable unit rather than something spread across a route
handler. Preserved verbatim from week 1: the two rules folded into the UPDATE,
the single `conn.transaction()` wrapper, `ORDER BY sku` for fixed lock
ordering, and `GREATEST(qty - $2, 0)`. Verified end to end: three scans of two
items produced two grouped receipt lines and decremented stock by exactly 2
and 1, and all three refusal paths were distinguishable.
Depends on: Step 14.

## Step 16 - `app/data/analytics_repo.py` [DONE]

`log_scan`, `claim_recompute`, `count_window`, `publish_ranking`,
`window_state`, `top_items`, `below_threshold`. The low-stock query lives here
rather than with transactions: `stock` is written by the transactions repo and
read by this one, which is fine because both are inside the data layer.
Depends on: Step 11.

---

## D. Business layers

## Step 17 - `app/analytics/service.py` [DONE]

`record_scan`, `popular_items`, `low_stock`. Written before the transactions
service because transactions depends on it. Item names for the ranking come
from the in-memory catalog rather than a second query, as in week 1.
Depends on: Steps 13, 16.

## Step 18 - `app/transactions/service.py` [DONE]

`list_items`, `start`, `scan`, `complete`, `get`, plus a private
`_explain_write_refusal` that turns a refused write into the right domain
error. That helper is the piece that makes the 404/409/`EMPTY_BASKET`
three-way distinction readable in one place instead of duplicated across two
routes.
Depends on: Steps 13, 15, 17.

---

## E. API layer

## Step 19 - `app/api/error_handlers.py` [DONE]

A dict mapping each `DomainError` subclass to a status code, and one handler
rendering `ApiError`. **The only file in the project that mentions an HTTP
status code.**
Depends on: Step 9.

## Step 20 - `app/api/wiring.py` [DONE]

Two module-level slots the routers read. Exists as its own file because
`main.py` imports the routers, so routers importing `main` would be circular.
Depends on: Steps 17, 18.

## Steps 21-24 - The four routers [DONE]

`transactions.py` (four routes), `catalog.py`, `inventory.py`,
`analytics.py`. Each route is one line: read the request, call a service,
return the result.
Depends on: Step 20.

## Step 25 - `app/main.py` [DONE]

The composition root, and the only file that knows how the layers fit
together. Lifespan opens the pool, builds the three repositories, hands those
to the two services, and populates `wiring`.
Depends on: Steps 19-24.

## Step 26 - `app/serve.py` [DONE]

Copied verbatim from week 1, h2c-refusal patch included. Confirmed with
`diff`. Without this the Java client upgrades to HTTP/2 and the server
collapses under stress - week 1 spent real time finding that, and it would
have been easy to lose by rewriting the file.
Depends on: Step 25.

---

## F. Tests

## Step 27 - `tests/test_services.py` [DONE]

Seven tests against stand-in repositories: unknown SKU, closed basket, missing
basket, a successful scan feeding the popularity window, empty basket,
already-paid basket, and that the ranking rebuilds only when due.

**Hit a configuration problem:** pytest could not import `app`. Fixed with
`pythonpath = ["."]` in the pytest config rather than installing the package.
All seven pass in 0.09s with no database and no web server, which is the
concrete demonstration that the rules are now separable from the framework.
Depends on: Steps 17, 18.

---

## G. Verification and benchmarking

## Step 28 - Smoke test all seven endpoints [DONE]

**Found a real defect:** the server crashed on startup with
`NameError: name 'TransactionService' is not defined` - Step 25 used the class
without importing it. Nothing before this point would have caught it, because
no earlier step actually started the app. Added the import.

After that, all seven endpoints returned correct shapes with correct camelCase,
and completion grouped three scans of two items into two receipt lines. Also
checked every error path: unknown SKU 404, closed basket 409, missing basket
404, double payment 409, blank stationId 400, empty basket 409
`EMPTY_BASKET`, missing basket GET 404.
Depends on: Step 26.

## Step 29 - Default run: 10 stations, 60s [DONE]

5,987 transactions (99.7/sec), 62,947 items (1,048.2/sec), 0.00% errors.
SCAN p95 9.49ms, COMPLETE p95 20.95ms - both marginally better than week 1.
Invariant afterwards: 0 negative stock, all 2,000 SKUs exact, 0 unexplained.
Depends on: Step 28.

## Step 30 - Stress run: 100 stations, 120s [DONE]

13,936 transactions (115.8/sec), 146,060 items (1,213.4/sec).
COMPLETE p95 2,712.42ms and p99 4,410.02ms, both slightly better than week 1.

**Reported 3 errors on COMPLETE_TRANSACTION where week 1 had zero.**
Investigated rather than assumed or re-run: the server logged nothing, the
database holds 13,939 `COMPLETED` transactions with none left open, and the
invariant shows zero unexplained violations. All 13,939 completions succeeded
server-side; the client's 10s timeout expired on three that took up to 9.90s.
Kept the run and documented it rather than re-running for a cleaner number.
Depends on: Step 29.

## Step 31 - Correctness invariant [DONE]

Default: 0 negative, 2,000 exact, 0 unexplained.
Stress: 0 negative, 1,999 exact, 1 explained by selling out, 0 unexplained.
The single non-exact SKU is SKU-000001, the most-scanned item, which floored
at zero - the same expected edge case week 1 documented, since the contract
defines no insufficient-stock response.
Depends on: Step 30.

---

## H. Documentation and submission

## Step 32 - `README.md` [DONE]

Setup, the layer diagram and dependency rules, the three decisions worth
defending, reproduction commands, the invariant query, and the full week 1
versus week 2 comparison including the three timeouts.
Depends on: Step 31.

## Step 33 - Layer boundary check [DONE]

Wrote a throwaway script walking every file's imports and asserting the four
rules: the API layer imports neither `asyncpg` nor any repository, the data
layer does not import the API layer, neither business layer imports FastAPI,
and analytics does not import transactions. No violations.
Depends on: Step 32.

## Step 34 - Commit and push [DONE]

Two commits: the monolith comment rewrite, then `layered/`. Pushed to
`origin/main`.
Depends on: Step 33.

## Step 35 - `QUALITY-ATTRIBUTES.md` and this file [DONE]

Written after the numbers existed, so every figure cited is from the report
JSONs rather than from memory.
Depends on: Step 31.

---

## Verification (referenced by Steps 29-31)

Correctness invariant query, run after each load-test run. The four-count
form from week 1's README, which separates a genuine violation from the
expected sold-out case:

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

`unexplained_violations` must be **zero**.

**Known edge case, not a bug:** the contract has no insufficient-stock error
path, so a hot SKU whose cumulative demand exceeds its starting 10,000 units
floors at zero rather than going negative. The load client's Zipf-skewed
sampling makes this near-certain in stress mode. Week 1 saw it on SKU-000001
and so did this week.

## Benchmark protocol

Both runs must start from an identical state or they are not comparable:

1. `cd ../monolith && docker compose down` - stop week 1's Postgres so the two
   databases are not sharing CPU and page cache.
2. Confirm nothing is holding port 8080. Week 1's plan records zombie server
   processes as a real source of skewed runs.
3. `./scripts/reset_db.sh`
4. Start the server with `uv run python -m app.serve`.
5. Run the client with `--reportDir=../layered/reports`.
6. Run the invariant query.
7. Repeat from 3 for the second parameter set.

## What did not change, deliberately

Listed because the value of this week's comparison rests on it:

- The API contract, and every response shape.
- Every SQL statement, including the guards folded into mutating statements,
  the fixed SKU lock ordering, and `GREATEST(qty - $2, 0)`.
- `schema.sql`, byte-identical.
- The seed data: 2,000 SKUs, 10,000 units, same price formula.
- `serve.py` and its h2c patch, byte-identical.
- Pool sizing, and Postgres `max_connections`.
- All five runtime dependency versions, plus `starlette` pinned back to
  week 1's (Step 5).
- Load parameters: 10/60 and 100/120, matching week 1 rather than the course
  README's suggested 200/180.

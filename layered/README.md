# Self-Checkout, Layered (Week 2)

Layered implementation of the self-checkout API contract defined in
`../spec/self-checkout-openapi.yaml`.

Same contract, same stack, and same behaviour as
[week 1's monolith](../monolith) - only the internal structure changed.
Stack: Python 3.12 + FastAPI + PostgreSQL, raw SQL via `asyncpg` (no ORM),
dependencies managed with `uv`.

## Status

All 7 endpoints implemented. Both required load runs complete, and the
correctness invariant holds in both.

| | Default (10 stations, 60s) | Stress (100 stations, 120s) |
|---|---|---|
| Transactions | 5,987 (99.7/sec) | 13,936 (115.8/sec) |
| Items scanned | 62,947 (1,048.2/sec) | 146,060 (1,213.4/sec) |
| SCAN_ITEM p95 | 9.49ms | 19.37ms |
| COMPLETE_TRANSACTION p95 | 20.95ms | 2,712.42ms |
| Error rate | 0.00% | 0.02% (see below) |

Raw reports are in `reports/`.

## The four layers

```
HTTP request
     |
     v
  app/api/            routing and error mapping
     |                no SQL, never imports asyncpg
     v
  app/transactions/   app/analytics/      the business rules
     |                      |             no FastAPI, no SQL
     v                      v
  app/data/           repositories: every SQL statement lives here
     |
     v
  PostgreSQL
```

**API layer** (`app/api/`) reads the request, calls a service, returns the
result. Its routes are two or three lines each.

**Transactions layer** (`app/transactions/`) holds the checkout rules: what
may be scanned, what may be paid for, and which error the customer gets
when the answer is no.

**Analytics layer** (`app/analytics/`) holds the reporting rules: what
counts as popular, and what counts as low stock.

**Database access layer** (`app/data/`) owns every SQL statement in the
project, and hides that the database is Postgres at all.

`config.py`, `errors.py` and `schemas.py` sit at the top of `app/` as a
shared kernel. They depend on nothing and any layer may use them.

### Dependency rules

- The API layer never imports `asyncpg` and never touches a repository.
- The data layer never imports anything from `app/api/`.
- `transactions/` may call `analytics/`, because every scan feeds the
  popularity window. Never the reverse.

### Three decisions worth defending

**`GET /items` lives in the transactions layer, and
`GET /inventory/low-stock` lives in analytics.** The assignment names four
layers but the API has four endpoint groups. Catalog reads join transactions
because scanning depends on catalog pricing and they are one concern; low
stock joins analytics because it is a read-only reporting view over data
transactions writes.

**Services are built once at startup, not injected per request.**
`main.py` constructs every repository and service inside the lifespan and
stores them in `app/api/wiring.py`. FastAPI's `Depends` would resolve on
every request, which at 1,200 scans a second is real work for objects that
never change. Constructor injection still gives the tests their seam.

**Business layers return the same Pydantic models the API serialises.**
A parallel set of domain dataclasses with identical fields would be
ceremony, not architecture, and would add a mapping step to all seven
endpoints. The tradeoff is that the business layers know the response field
names. `schemas.py` sits above all four layers rather than inside `api/`,
so a service importing it is not reaching upward into the delivery layer.

## What moved, compared to week 1

Week 1's `scan_item` route did six things in one function: catalog lookup,
a conditional UPDATE, a second query to tell 404 from 409, a line-item
INSERT, an analytics call, and response assembly.

Those six things now live in three files. The route is one line, the rules
are in `transactions/service.py`, and the SQL is in
`data/transactions_repo.py`.

The other structural change is that domain errors no longer carry HTTP
status codes. Week 1's `ConflictError` had `status_code = 409` on the class,
so checkout logic knew how it would be rendered. Now `app/errors.py` is
framework-free and `app/api/error_handlers.py` is the only file in the
project that mentions a status code.

## Requirements

- Docker (for Postgres)
- [`uv`](https://docs.astral.sh/uv/)
- JDK 21+, only to build and run the load client

## Setup

This folder is self-contained: it runs its own Postgres and does not depend
on `../monolith` for anything.

```bash
docker compose up -d        # Postgres on host port 5434
uv sync
cp .env.example .env
./scripts/reset_db.sh       # 2000 SKUs, 10,000 units each
```

Port 5434 rather than 5433 so week 1's database can stay running alongside
this one without a collision.

## Running the server

```bash
uv run python -m app.serve
```

Use this launcher rather than starting `hypercorn` directly. The Java load
client offers to upgrade every request to HTTP/2, and if hypercorn accepts,
the client switches protocol and then collapses under sustained load.
`app/serve.py` refuses the upgrade. There is no config flag for this, hence
the patch. Week 1's README documents the full investigation.

## Tests

```bash
uv run pytest
```

Seven tests covering the checkout and reporting rules. They use stand-in
repositories that hold data in plain Python, so they need no database and no
web server, and run in about a tenth of a second.

That is the concrete payoff of the layering: in week 1 these rules were
inside FastAPI route handlers and could only be tested by starting a server
and a database.

**There is no linter configured for this project**, so "run the linter" is
not something that can be done here.

## Reproducing the load tests

Build the client once. This machine's default Java is 17 and the client
needs 21+:

```bash
cd ../load-client
export JAVA_HOME=/Library/Java/JavaVirtualMachines/temurin-21.jdk/Contents/Home
export PATH="$JAVA_HOME/bin:$PATH"
./build.sh
```

Stop week 1's Postgres first, so the two databases are not sharing CPU:

```bash
cd ../monolith && docker compose down
```

Reset the database and restart the server before **each** run, or the two
are not comparable.

```bash
# default
cd ../layered && ./scripts/reset_db.sh && uv run python -m app.serve
cd ../load-client
./run.sh --baseUrl=http://localhost:8080 --stations=10 --duration=60 --reportDir=../layered/reports

# stress
cd ../layered && ./scripts/reset_db.sh && uv run python -m app.serve
cd ../load-client
./run.sh --baseUrl=http://localhost:8080 --stations=100 --duration=120 --reportDir=../layered/reports
```

These are the same parameters week 1 used. The course README suggests
200/180 for stress, but matching week 1 is what makes the comparison below
mean anything.

### Checking correctness after a run

```bash
docker exec self-checkout-db-layered psql -U checkout -d self_checkout -c "
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

Results from the submitted runs:

| | negative stock | exact | explained by selling out | unexplained |
|---|---|---|---|---|
| Default | 0 | 2000 | 0 | 0 |
| Stress | 0 | 1999 | 1 | 0 |

The single non-exact SKU under stress is SKU-000001, the most-scanned item,
which sold out. The contract defines no "insufficient stock" response, so
stock floors at zero rather than going negative. Week 1 documented the same
edge case.

## Week 1 versus week 2

Same hardware, same client, same parameters. The only difference is the
architecture.

### Default (10 stations, 60s)

| | monolith | layered | change |
|---|---|---|---|
| Transactions/sec | 98.3 | 99.7 | +1.4% |
| Items/sec | 1,032.6 | 1,048.2 | +1.5% |
| START p95 | 3.79ms | 3.67ms | -0.12ms |
| SCAN p95 | 9.68ms | 9.49ms | -0.19ms |
| SCAN p99 | 22.02ms | 20.64ms | -1.38ms |
| COMPLETE p95 | 21.48ms | 20.95ms | -0.53ms |
| COMPLETE p99 | 30.18ms | 29.10ms | -1.08ms |
| Error rate | 0.00% | 0.00% | - |

### Stress (100 stations, 120s)

| | monolith | layered | change |
|---|---|---|---|
| Transactions/sec | 117.0 | 115.8 | -1.0% |
| Items/sec | 1,222.2 | 1,213.4 | -0.7% |
| START p95 | 6.59ms | 7.13ms | +0.54ms |
| SCAN p95 | 18.39ms | 19.37ms | +0.98ms |
| SCAN p99 | 55.45ms | 52.85ms | -2.60ms |
| COMPLETE p95 | 2,722.91ms | 2,712.42ms | -10.5ms |
| COMPLETE p99 | 4,486.00ms | 4,410.02ms | -76ms |
| Error rate | 0.00% | 0.02% | +3 requests |

### Reading these numbers

**Layering cost nothing measurable.** Every difference above is inside
run-to-run noise, and the default run is marginally faster than week 1
rather than slower. This is the expected result: moving code between files
does not add work at runtime, and the services are constructed once at
startup rather than per request.

The architecture did not change *where* the time goes either. Completion is
still the expensive operation under load, for the same reason as week 1: it
holds a database transaction while it decrements stock row by row, and at
100 stations those row locks queue up. That is the cost of the data
integrity guarantee, and the layering neither helped nor hurt it.

**About the three stress-run errors.** The load client counted 3 failed
completions out of 13,939. Investigating rather than assuming:

- The server logged no errors or exceptions at all.
- The database contains 13,939 `COMPLETED` transactions and zero left open.
- The invariant check shows zero unexplained violations.

So all 13,939 completions succeeded server-side. The client gave up waiting
on three of them: its per-request timeout is 10s and the slowest completion
took 9.90s. These are timeouts on an already-slow tail, not failures.

It is an honest difference from week 1's 0.00% all the same. The p95 and p99
for completion are slightly *better* than week 1, so the tail did not get
worse in general - three requests happened to land past the cutoff on this
run where week 1's worst landed just under it.

**Popular items stayed stable**, which is the cross-week check the course
README asks for. SKU-000001 ranks first in both weeks, with the same
Zipf-shaped tail, confirming the analytics behaviour survived the move into
its own layer.

## Project layout

```
layered/
├── app/
│   ├── main.py                    # composition root: builds and wires every layer
│   ├── serve.py                   # launcher, refuses HTTP/2 upgrades
│   ├── config.py                  # settings from .env
│   ├── errors.py                  # domain errors, no HTTP
│   ├── schemas.py                 # request/response shapes, shared by all layers
│   ├── api/                       # ---- API LAYER ----
│   │   ├── error_handlers.py      # domain error -> status code
│   │   ├── wiring.py              # where routers find the services
│   │   └── routers/               # one file per endpoint group
│   ├── transactions/service.py    # ---- TRANSACTIONS LAYER ----
│   ├── analytics/service.py       # ---- ANALYTICS LAYER ----
│   └── data/                      # ---- DATABASE ACCESS LAYER ----
│       ├── pool.py
│       ├── catalog_repo.py
│       ├── transactions_repo.py
│       └── analytics_repo.py
├── db/{schema.sql, seed.py}
├── scripts/reset_db.sh
├── tests/test_services.py
├── reports/                       # the two submitted runs
├── docker-compose.yml             # Postgres on 5434
└── pyproject.toml
```

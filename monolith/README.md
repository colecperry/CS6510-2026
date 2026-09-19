# Self-Checkout Monolith (Week 1)

Monolithic implementation of the self-checkout API contract defined in
`../spec/self-checkout-openapi.yaml`.

Stack: Python 3.12 + FastAPI + PostgreSQL, using raw SQL via `asyncpg`
(no ORM). Dependencies and the virtual environment are managed with `uv`.

## Status

All 7 endpoints from the spec are implemented, and both required load-test
runs pass at a 0.00% error rate.

| | Default (10 stations, 60s) | Stress (100 stations, 120s) |
|---|---|---|
| Transactions | 5,905 (98.3/sec) | 14,085 (117.0/sec) |
| Items scanned | 62,027 (1,032.6/sec) | 147,182 (1,222.2/sec) |
| Error rate | 0.00% | 0.00% |
| SCAN_ITEM p95 | 9.68ms | 18.39ms |
| COMPLETE_TRANSACTION p95 | 21.48ms | 2,722.91ms |

Raw reports are in `reports/`.
Analysis of these numbers, and the design trade-offs behind them, is in
[QUALITY-ATTRIBUTES.md](QUALITY-ATTRIBUTES.md).

The correctness invariant held in both runs: no SKU went negative, and
there were no unexplained discrepancies between stock decremented and
units sold. The one exception under stress is documented in the analysis
(SKU-000001 sold out, which the spec has no error path for).

## Requirements

- Docker (for Postgres)
- [`uv`](https://docs.astral.sh/uv/) (Python package/environment manager)
- JDK 21+, but only to build and run the load client (see below)

## Setup

Start Postgres (runs in Docker on host port `5433`, not the default 5432,
to avoid colliding with any other local Postgres install):

```bash
docker compose up -d
```

Install dependencies (creates `.venv` automatically):

```bash
uv sync
```

Copy the environment template (defaults already match `docker-compose.yml`):

```bash
cp .env.example .env
```

Build the database schema and seed data (2000 SKUs, 10,000 units of stock
each):

```bash
./scripts/reset_db.sh
```

Run this same command any time you want to wipe and rebuild the database
back to a clean starting state. Every load-test run below starts with it,
otherwise runs aren't comparable.

## Running the server

```bash
uv run python -m app.serve
```

Use this launcher rather than starting `hypercorn` directly. It patches
hypercorn to refuse HTTP/2 upgrades, which the server needs in order to
work with the load client.

### Why the custom launcher, and why not `uvicorn`

The load client is a Java `java.net.http.HttpClient`, which probes every
request with an `Upgrade: h2c` header, offering to switch to HTTP/2. That
one header broke two different servers in two different ways:

1. **uvicorn silently drops the request body.** Both of its HTTP
   implementations (`h11` and `httptools`) log `Unsupported upgrade
   request` and then fail to deliver the body to the app, producing
   `422 Field required` on every request. A raw TCP capture confirmed the
   client's request was well-formed, so the fault was entirely
   server-side. `uvicorn` was removed from this project's dependencies so
   nobody reaches for it by habit and hits this again.

2. **hypercorn accepts the upgrade, which collapses under load.**
   Hypercorn ignores the probe on requests that have a body, so every POST
   is served as HTTP/1.1. But the client's one bodyless `GET /items` at
   startup does trigger a real upgrade, after which the client sends
   everything over HTTP/2. Under sustained stress that produced
   stream-limit errors and then mass timeouts. Plain HTTP/1.1 handles the
   identical concurrency with zero errors, so `app/serve.py` refuses the
   upgrade outright. There is no config flag for this, hence the patch.

## Reproducing the load tests

Build the client once. This machine's default Java is 17, but the client
uses virtual threads and needs 21+, so point `JAVA_HOME` at a JDK 21
install:

```bash
cd ../load-client
export JAVA_HOME=/Library/Java/JavaVirtualMachines/temurin-21.jdk/Contents/Home
export PATH="$JAVA_HOME/bin:$PATH"
./build.sh
```

With the server running in another terminal, reset the database and run.
Default parameters:

```bash
cd ../monolith && ./scripts/reset_db.sh
cd ../load-client
./run.sh --baseUrl=http://localhost:8080 --stations=10 --duration=60 --reportDir=../monolith/reports
```

Stress mode:

```bash
cd ../monolith && ./scripts/reset_db.sh
cd ../load-client
./run.sh --baseUrl=http://localhost:8080 --stations=100 --duration=120 --reportDir=../monolith/reports
```

### Checking correctness after a run

The load client reports latency, not correctness. To verify the invariant
that actually matters, run this against the database afterwards. It should
report zero negative stock and zero unexplained violations:

```bash
docker exec self-checkout-db psql -U checkout -d self_checkout -c "
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

## Project layout

```
monolith/
├── QUALITY-ATTRIBUTES.md  # characteristics analysis + trade-offs
├── app/
│   ├── main.py            # wires everything together; app startup/shutdown
│   ├── serve.py           # server launcher (see "Running the server" above)
│   ├── config.py          # typed settings, read from .env
│   ├── pool.py            # asyncpg connection pool
│   ├── catalog_cache.py   # in-memory catalog, loaded once at startup
│   ├── popularity.py      # hopping-window popular-items tracking
│   ├── models.py          # Pydantic models for every request/response shape
│   ├── errors.py          # ApiException hierarchy -> spec-correct error JSON
│   └── routers/
│       ├── catalog.py      # GET /items
│       ├── transactions.py # POST /transactions, .../items, .../complete, GET /transactions/{id}
│       ├── inventory.py    # GET /inventory/low-stock
│       └── analytics.py    # GET /analytics/popular-items
├── db/
│   ├── schema.sql       # table definitions (DROP + CREATE, idempotent)
│   └── seed.py          # rebuilds schema.sql and loads starting data
├── reports/             # load-test results (the submitted runs)
├── scripts/
│   └── reset_db.sh      # convenience wrapper around `db/seed.py`
├── docker-compose.yml    # Postgres, host port 5433
└── pyproject.toml        # dependencies (managed via `uv`)
```

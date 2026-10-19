# Self-Checkout, Service-Based (Week 5)

The same self-checkout API contract from `../spec/self-checkout-openapi.yaml`, split into separate domain services over one shared database.

Built on [week 4's pipeline implementation](../pipeline), so the comparison below isolates what distribution cost.

## The services

**The system has three domain services, plus an entry point.**

| Service       | Business function                     | Owns                                                                                                                | Port |
|---------------|---------------------------------------|---------------------------------------------------------------------------------------------------------------------|------|
| **checkout**  | Ringing up a sale                     | `POST /transactions`, `POST /transactions/{id}/items`, `POST /transactions/{id}/complete`, `GET /transactions/{id}` | 8081 |
| **inventory** | What we sell, and what is running out | `GET /items`, `GET /inventory/low-stock`                                                                            | 8082 |
| **analytics** | Reporting on trends                   | `GET /analytics/popular-items`, plus the background rebuild                                                         | 8083 |

**gateway** (8080) is not a domain service.
It receives every request and forwards it to whichever service owns the path.
It exists because the load client can only be pointed at one address.
It holds no business rules and opens no database connection, which is enforced by a test rather than by convention.

Each service is its own project with its own dependencies and its own entry point, and each can be started with the other two stopped.

## No service calls another

The brief forbids it, so where two services need the same information they use the shared database.

```
checkout ──writes──► stock ◄──reads── inventory
checkout ──writes──► scan_log ◄──reads── analytics
```

Those two tables are the entire conversation between services.

The clearest case is the popular-items ranking.
A shopper scans an item at checkout; the ranking is served by analytics.
Checkout writes the scan to `scan_log` and returns.
Analytics polls the database four times a second asking whether enough scans have accumulated, and when they have, counts them and publishes a ranking.
Neither service knows the other exists.

### How the shared data access is organised

`common/` is a library holding the connection pool and the four repositories, installed by each service as a local dependency.
Each service imports only what it uses.

| Repository     | Used by                                                                 |
|----------------|-------------------------------------------------------------------------|
| `catalog_repo` | all three                                                               |
| `baskets_repo` | checkout                                                                |
| `stock_repo`   | inventory reads; checkout writes stock inside `baskets_repo.complete()` |
| `scans_repo`   | checkout writes, analytics reads                                        |

A shared library is itself a form of coupling: changing a query means redeploying every service that uses it.
That is the accepted cost, and it is the characteristic trade of service-based architecture against microservices, where each service would own its own tables and its own copy of the code.

**One deliberate exception:** the stock reduction lives in `baskets_repo.complete()` rather than `stock_repo`, because marking a basket paid and taking its items out of stock must happen together or not at all.
The file boundary follows the transaction boundary, not the table name.

## Results

Same client, same parameters, same machine as the previous weeks.
Reports in `reports/`.

### Default (10 stations, 60s)

|                  | monolith | layered | pipeline | service-based |
|------------------|----------|---------|----------|---------------|
| Transactions/sec | 98.3     | 99.7    | 123.6    | **45.1**      |
| Items/sec        | 1,032.6  | 1,048.2 | 1,300.9  | **471.1**     |
| START p95        | 3.79ms   | 3.67ms  | 4.58ms   | **29.05ms**   |
| SCAN p95         | 9.68ms   | 9.49ms  | 7.10ms   | **28.30ms**   |
| COMPLETE p95     | 21.48ms  | 20.95ms | 31.95ms  | **30.58ms**   |
| Error rate       | 0.00%    | 0.00%   | 0.00%    | **0.00%**     |

### Stress (100 stations, 120s)

|                  | monolith   | layered    | pipeline   | service-based |
|------------------|------------|------------|------------|---------------|
| Transactions/sec | 117.0      | 115.8      | 124.5      | **37.6**      |
| Items/sec        | 1,222.2    | 1,213.4    | 1,313.7    | **397.4**     |
| START p95        | 6.59ms     | 7.13ms     | 7.07ms     | **588.57ms**  |
| SCAN p95         | 18.39ms    | 19.37ms    | 10.88ms    | **490.93ms**  |
| COMPLETE p95     | 2,722.91ms | 2,712.42ms | 2,726.74ms | **486.44ms**  |
| COMPLETE p99     | 4,486.00ms | 4,410.02ms | 4,608.61ms | **807.08ms**  |
| Error rate       | 0.00%      | 0.02%      | 0.02%      | **0.00%**     |

### Reading these numbers

**Throughput fell by about two thirds**, from 123.6 to 45.1 transactions a second at the default load.
Every request now crosses an extra process, and that process is written in the same language and framework as the services rather than being a purpose-built proxy.

**At default load, the three operations converged on roughly 29ms.**
Starting a basket and scanning an item each gained about 22ms; completion, already the slowest, barely moved.
That pattern is a fixed per-request cost rather than anything to do with the service split, and the gateway hop is the obvious candidate.

**Under stress, something more interesting happened.**
Completion got *better*, not worse: p95 fell from 2,726ms to 486ms, and p99 from 4,608ms to 807ms.
The error rate went to zero, where weeks 2 and 4 each lost three requests to timeouts.

The explanation is that the gateway became the bottleneck, and in doing so it throttled the system.
Only 37.6 transactions a second reached the database, against 124.5 before.
Completion holds row locks while decrementing stock, and far fewer concurrent completions means far less contention for those locks.
The slowest operation in the system got five times faster because the system as a whole got three times slower.

That is worth stating plainly: **this architecture did not make completion more efficient. It made it less busy.**
The improvement is a side effect of the throughput collapse, not a fix.

**Correctness was perfect in both runs**, which is the result that matters most and the one the brief grades.

## Correctness

|         | negative stock | exact | explained by selling out | unexplained |
|---------|----------------|-------|--------------------------|-------------|
| Default | 0              | 2000  | 0                        | 0           |
| Stress  | 0              | 2000  | 0                        | 0           |

Zero scans dropped by the checkout service, and zero errors in any service log, in both runs.

This is the first week where the stress run produced no sold-out SKU and no client timeouts.
Both are consequences of the lower throughput rather than of better design.

## Requirements

- Docker, for the database
- [`uv`](https://docs.astral.sh/uv/)
- A JDK 21 or later that runs natively, only to build and run the load client

## Setup

```bash
cd service-based
docker compose up -d        # one Postgres, host port 5436
uv sync --all-packages      # note: --all-packages, this is a workspace
cp .env.example .env
./scripts/reset_db.sh       # 2,000 items, 10,000 units each
./scripts/start_all.sh      # all four processes
```

`uv sync` on its own only syncs the workspace root, which has no dependencies.
`--all-packages` is what installs the five members.

To stop:

```bash
./scripts/stop_all.sh
docker compose down
```

### Starting one service on its own

Each service is independently deployable, which is easiest to see by starting one alone:

```bash
uv run --directory inventory python -m app.serve     # answers on 8082
```

It will serve `GET /items` and `GET /inventory/low-stock` with the other three processes stopped.

## Tests

```bash
uv run --directory checkout pytest     # 10, the checkout rules and the scan writer
uv run --directory inventory pytest    # 4, threshold handling
uv run --directory analytics pytest    # 3, when a ranking is rebuilt
uv run pytest tests/                   # 19, the service boundaries
```

**36 tests, no database and no web server.**

The boundary suite is the one worth knowing about.
It parses every service's source and fails if a service imports another service, mentions another service's address, or carries an HTTP client at all.
It also fails if the gateway imports the data library.
That rule is the one thing the brief is most explicit about, and a system that broke it would behave identically to one that did not, so it is checked mechanically rather than by eye.

**There is no linter configured for this project**, so "run the linter" is not something that can be done here.

## Reproducing the load tests

Build the client once with a JDK 21 or later:

```bash
cd ../load-client
export JAVA_HOME=/opt/homebrew/opt/openjdk@21/libexec/openjdk.jdk/Contents/Home
export PATH="$JAVA_HOME/bin:$PATH"
./build.sh
```

Stop the other weeks' database containers so nothing competes for the machine:

```bash
docker stop self-checkout-db self-checkout-db-layered self-checkout-db-pipeline
```

Reset the database and restart the services before **each** run, or the two are not comparable:

```bash
cd ../service-based && ./scripts/stop_all.sh && ./scripts/reset_db.sh && ./scripts/start_all.sh
cd ../load-client
./run.sh --baseUrl=http://localhost:8080 --stations=10 --duration=60 --reportDir=../service-based/reports

cd ../service-based && ./scripts/stop_all.sh && ./scripts/reset_db.sh && ./scripts/start_all.sh
cd ../load-client
./run.sh --baseUrl=http://localhost:8080 --stations=100 --duration=120 --reportDir=../service-based/reports
```

### Checking correctness after a run

```bash
docker exec self-checkout-db-services psql -U checkout -d self_checkout -c "
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

Also check the checkout log for dropped scans, since scan writing is asynchronous and a drop would silently undercount the ranking:

```bash
grep dropped logs/checkout.log
```

## Design notes

**The scan writer batches.** At 50 concurrent scans it produces one database write rather than 50. Carried from week 4, and it lives in the checkout service because that is where scans arrive.

**The rebuild claim is a single statement.** Asking how far the scan log has got, deciding whether that is far enough, and claiming the rebuild all happen inside one `UPDATE`, so two polls can never both rebuild the same window.

**Polling runs four times a second.** A rebuild is due every 500 scans; at full load that is roughly every 400ms. Polling faster would buy almost nothing, and each poll costs one cheap statement.

**Connection pools are split by traffic**: 100 for checkout, 10 each for inventory and analytics. The previous weeks gave a single process 120 against a server limit of 200, and three processes each taking that would exhaust it.

**Each service loads its own copy of the catalogue.** Three copies of 2,000 immutable rows is waste, and it is cheaper than the alternative, which would be one service owning the catalogue and the others calling it.

## Project layout

```
service-based/
├── common/                     the shared library, installed by each service
│   └── common/
│       ├── config.py           settings, read once from .env at the root
│       ├── errors.py           domain errors, no HTTP
│       ├── schemas.py          the wire contract
│       ├── error_handlers.py   domain error -> status code, shared so services agree
│       └── data/               pool, catalog_repo, baskets_repo, stock_repo, scans_repo
├── gateway/     app/{main,serve,routes}.py          port 8080, no database
├── checkout/    app/{main,serve,service,ingest,wiring}.py, api/, tests/
├── inventory/   app/{main,serve,service,wiring}.py, api/, tests/
├── analytics/   app/{main,serve,service,rebuilder,wiring}.py, api/, tests/
├── db/{schema.sql, seed.py}    schema byte-identical to the previous weeks
├── scripts/{reset_db.sh, start_all.sh, stop_all.sh}
├── tests/test_boundaries.py    the no-service-calls-another check
├── reports/                    the two submitted runs
├── docker-compose.yml          Postgres on 5436
└── pyproject.toml              the workspace, listing the five members
```

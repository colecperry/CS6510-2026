# Self-Checkout, Microservices (Week 6)

The same self-checkout API contract from `../spec/self-checkout-openapi.yaml`, split into microservices that each own their data.

Week 5 divided the system into three services, but they still shared one database.
That meant the coupling had moved rather than gone: any service could read any table, and paying for a basket was still a single transaction that marked the basket paid and reduced stock together.

This week each service gets its own database.
That one change removes the transaction, because the two halves of a payment now live in different Postgres instances and nothing can span them.
Paying becomes a sequence of steps across two services, with a record of how far it got and a way back if a step fails.

Built on [week 5's service-based implementation](../service-based), copied and verified identical before any change, so every difference below is deliberate.

## The microservices

```
load client
     |
     v
  gateway :8080          routes by URL path, holds no data and no rules
     |
     +---> checkout  :8081  ----> checkout database  :5437
     |                             baskets, basket lines, payment progress
     |
     +---> inventory :8082  ----> inventory database :5438
     |                             the catalogue, stock levels, applied requests
     |
     +---> analytics :8083  ----> analytics database :5439
                                   the scan stream and the published ranking
```

| Service       | Business function    | Endpoints                                                                                                           | Its data store holds                                                    |
|---------------|----------------------|---------------------------------------------------------------------------------------------------------------------|-------------------------------------------------------------------------|
| **checkout**  | Selling              | `POST /transactions`, `POST /transactions/{id}/items`, `POST /transactions/{id}/complete`, `GET /transactions/{id}` | `transactions`, `transaction_items`, `payment_saga`, a catalogue copy   |
| **inventory** | Stock                | `GET /items`, `GET /inventory/low-stock`                                                                            | `catalog` (authoritative), `stock`, `applied_requests`                  |
| **analytics** | Reporting            | `GET /analytics/popular-items`                                                                                      | `scan_log`, `popularity_state`, `popularity_snapshot`, a catalogue copy |
| **gateway**   | Not a domain service | forwards all of the above                                                                                           | nothing. No database driver, no shared library, no business rules       |

The gateway exists because the load client can only be pointed at one address.
It is the caller-facing tier rather than a fourth microservice, and the assignment permits it explicitly.

### Who owns what, and what had to be copied

| Table                 | checkout  | inventory | analytics |
|-----------------------|-----------|-----------|-----------|
| `catalog`             | copy      | **owner** | copy      |
| `stock`               |           | **owner** |           |
| `transactions`        | **owner** |           |           |
| `transaction_items`   | **owner** |           |           |
| `payment_saga`        | **owner** |           |           |
| `applied_requests`    |           | **owner** |           |
| `scan_log`            |           |           | **owner** |
| `popularity_state`    |           |           | **owner** |
| `popularity_snapshot` |           |           | **owner** |

The catalogue is the one thing copied three ways.
All three services need item names and prices, none may read another's store, and catalogue rows never change while the system runs, so the copies cannot drift apart.
The alternative was a call to inventory on every scan, which runs about 1,300 times a second and is the operation the previous weeks worked hardest to keep fast.

`tests/test_boundaries.py` checks the rest mechanically: no service names a table it does not own, no service constructs another's settings, and only checkout carries an HTTP client.

## How a payment works now

Paying changes two databases, so it runs as a saga: a sequence of local transactions, each recorded, with a way to undo the ones already applied.

```
1. claim the basket and write down what this payment intends to reduce   (checkout db)
2. ask inventory to take that stock off the shelf                        (inventory db)
3. mark the basket paid and the saga done                                (checkout db)
```

**Stock goes first on purpose.**
Crashing after step 2 leaves the shop believing it has less than it does, which cannot oversell.
The other order would hand a shopper a receipt for stock still on the shelf.

**A failed step is undone.**
If the stock call fails, checkout asks inventory to undo that key and refuses the payment.
Undoing something that was never applied changes nothing, which matters because a lost reply looks exactly like a lost request.

**A crash is repaired at the next startup.**
Compensation is code, and a dead process runs none.
What survives is the saga row, so checkout reads any left unfinished when it starts, asks inventory whether each one actually moved stock, and drives each to a completed sale or a full undo.
It does this before accepting traffic, because serving first would let a shopper scan into a basket whose stock had already been taken.

**A repeated instruction is harmless.**
Every stock change carries a key, and inventory records that key in the same transaction that moves the stock.
A second request with the same key changes nothing and reports success exactly as the first did.

### How a scan reaches reporting

Analytics owns the scan log, and checkout cannot write to it.
A scan is dropped onto an in-memory queue and the request ends; a background writer posts batches of up to 200 to analytics.
A shopper never waits for this, and if analytics is down the batch is counted as lost rather than retried, because a writer stuck retrying would back the queue up and start dropping scans anyway.

## Results

Same client, same parameters, same machine as the previous weeks.
Reports in `reports/`.

### Default (10 stations, 60s)

|                  | monolith | layered | pipeline | service-based | microservices |
|------------------|----------|---------|----------|---------------|---------------|
| Transactions/sec | 98.3     | 99.7    | 123.6    | 45.1          | **41.0**      |
| Items/sec        | 1,032.6  | 1,048.2 | 1,300.9  | 471.1         | **428.2**     |
| START p95        | 3.79ms   | 3.67ms  | 4.58ms   | 29.05ms       | **29.30ms**   |
| SCAN p95         | 9.68ms   | 9.49ms  | 7.10ms   | 28.30ms       | **29.96ms**   |
| COMPLETE p95     | 21.48ms  | 20.95ms | 31.95ms  | 30.58ms       | **47.05ms**   |
| Error rate       | 0.00%    | 0.00%   | 0.00%    | 0.00%         | **0.00%**     |

### Stress (100 stations, 120s)

|                  | monolith   | layered    | pipeline   | service-based | microservices |
|------------------|------------|------------|------------|---------------|---------------|
| Transactions/sec | 117.0      | 115.8      | 124.5      | 37.6          | **27.8**      |
| Items/sec        | 1,222.2    | 1,213.4    | 1,313.7    | 397.4         | **290.0**     |
| START p95        | 6.59ms     | 7.13ms     | 7.07ms     | 588.57ms      | **936.01ms**  |
| SCAN p95         | 18.39ms    | 19.37ms    | 10.88ms    | 490.93ms      | **749.83ms**  |
| COMPLETE p95     | 2,722.91ms | 2,712.42ms | 2,726.74ms | 486.44ms      | **734.76ms**  |
| Error rate       | 0.00%      | 0.02%      | 0.02%      | 0.00%         | **0.00%**     |

### Reading these numbers

**Throughput fell again, by 9% at default load and 26% under stress.**
The reason is arithmetic rather than mystery.
The load client runs a fixed number of stations, each doing one transaction at a time, so throughput is stations divided by how long a transaction takes.
Paying now costs an extra network round trip and a second database commit, so a transaction takes longer, so fewer finish per second.

**Completion got slower, and that is the honest cost of the split.**
COMPLETE p95 rose from 30.58ms to 47.05ms at default load.
One local transaction became two commits in different databases with an HTTP call between them.

**Scanning barely moved**, from 28.30ms to 29.96ms, because the scan path gained no synchronous work.
Scans still go to a queue and the shopper's request ends there; only the destination changed from a shared table to another service.

**The gateway is still the ceiling.**
Measured during the runs, it sat at 87.3% of a CPU core at default load and 86.3% under stress, while the two services that do the real work sat well below it.
Week 5 proved the gateway was the bottleneck; this week it still is.

| CPU during the run | gateway   | checkout | inventory | analytics |
|--------------------|-----------|----------|-----------|-----------|
| Default            | **87.3%** | 76.9%    | 7.3%      | 25.6%     |
| Stress             | **86.3%** | 54.4%    | 5.0%      | 14.7%     |

Checkout jumped from 52.9% last week to 76.9% this week at the same load level, which is the saga showing up as real work rather than as an idea.

**Both runs had zero errors**, which is better than layered and pipeline managed under stress.

### Correctness

|         | negative stock | exact | explained by selling out | unexplained | payments compensated |
|---------|----------------|-------|--------------------------|-------------|----------------------|
| Default | 0              | 2000  | 0                        | 0           | 0                    |
| Stress  | 0              | 2000  | 0                        | 0           | 0                    |

Every unit sold came off the shelf, in both runs, across two databases that cannot see each other.

No payment was compensated and none was left unfinished, which means the failure machinery never ran during the measured runs.
It is exercised by the tests and by the drills described below, not by these numbers.

## Requirements

- Docker, for three Postgres containers
- [`uv`](https://docs.astral.sh/uv/)
- JDK 21+, only to build and run the load client

## Setup

Self-contained: its own databases, nothing shared with the other weeks.

```bash
docker compose up -d        # Postgres on 5437, 5438, 5439
uv sync --all-packages      # a workspace syncs only the root without the flag
cp .env.example .env
./scripts/reset_db.sh       # all three stores, 2000 SKUs, 10,000 units each
```

Ports 5437 to 5439 because 5433 through 5436 belong to the earlier weeks, so all five can run at once.

## Running

```bash
./scripts/start_all.sh      # gateway 8080, checkout 8081, inventory 8082, analytics 8083
./scripts/stop_all.sh
```

Each service is independently deployable and can be started on its own:

```bash
uv run --directory inventory python -m app.serve
```

`start_all.sh` is a convenience that saves four terminals, not a dependency.

Use these launchers rather than starting `hypercorn` directly.
The Java load client asks to switch to a newer version of HTTP on every request, and accepting makes the server collapse under sustained load.
Each service's `serve.py` refuses the switch, and week 1's README documents the full investigation.

## Tests

```bash
uv run pytest                              # cross-service boundary checks
cd checkout  && uv run pytest              # checkout, including the saga
cd inventory && uv run pytest              # inventory, including idempotency
cd analytics && uv run pytest              # the ranking rebuilder
```

45 tests. Most need no database and no web server.

Four do need the inventory database running, deliberately: the guarantee that a repeated stock change moves stock once comes from a Postgres `ON CONFLICT` clause, and a stand-in would only be testing the stand-in. They skip with a message when the database is not up.

**There is no linter configured for this project**, so "run the linter" is not something that can be done here.

## Reproducing the load tests

Build the client once. This machine's default Java is 17 and the client needs 21+:

```bash
cd ../load-client
export JAVA_HOME=/opt/homebrew/opt/openjdk@21/libexec/openjdk.jdk/Contents/Home
export PATH="$JAVA_HOME/bin:$PATH"
./build.sh
```

Stop the other weeks' containers first, so the databases are not sharing CPU:

```bash
docker stop self-checkout-db self-checkout-db-layered self-checkout-db-pipeline self-checkout-db-services
```

Reset and restart before **each** run, or the two are not comparable.

```bash
# default
cd ../microservices && ./scripts/reset_db.sh && ./scripts/start_all.sh
cd ../load-client
./run.sh --baseUrl=http://localhost:8080 --stations=10 --duration=60 --reportDir=../microservices/reports

# stress
cd ../microservices && ./scripts/stop_all.sh && ./scripts/reset_db.sh && ./scripts/start_all.sh
cd ../load-client
./run.sh --baseUrl=http://localhost:8080 --stations=100 --duration=120 --reportDir=../microservices/reports
```

### Checking correctness after a run

```bash
./scripts/check_invariant.sh
```

Units sold live in the checkout store and quantities in the inventory store, so this reads both and compares them outside the database.
It exits non-zero on any unexplained discrepancy, or on a payment left unfinished.

That is itself a finding worth noting: in every previous week this was one SQL query, because a join could see both halves.
The property did not get weaker, but the thing enforcing it moved out of the database and into a script.

### Exercising the failure paths

None of these happen during a normal load run, which is why they are worth doing by hand.

```bash
# a payment refused because inventory is unreachable
kill $(cat logs/inventory.pid)
curl -X POST http://localhost:8080/transactions/tx-1/complete     # 409 within ~2s, not a hang

# a repeated stock change
curl -X POST http://localhost:8082/internal/stock/reduce \
  -H 'Content-Type: application/json' \
  -d '{"idempotencyKey":"demo","lines":[{"sku":"SKU-000001","qty":5}]}'
# send it twice: the second returns {"applied":true,"alreadyApplied":true} and moves nothing

# a crash mid-payment: kill checkout between the stock call and the basket being marked paid, then restart it.
# Recovery runs before it accepts traffic and drives the saga to DONE or COMPENSATED.
```

## Project layout

```
microservices/
├── common/                        the wire contract only, no SQL
│   └── common/{config,errors,schemas,error_handlers,pool}.py
├── gateway/     app/{main,serve,routes}.py                         :8080
├── checkout/    app/
│   ├── data/{baskets_repo,catalog_repo,saga_repo}.py
│   ├── clients/{inventory_client,analytics_client}.py              outbound calls
│   ├── saga/{coordinator,recovery}.py                              <- the payment sequence
│   └── {main,serve,service,ingest,wiring}.py                       :8081
├── inventory/   app/
│   ├── data/{catalog_repo,stock_repo}.py
│   ├── api/{routes,internal_routes}.py
│   └── {main,serve,service,stock_mover,wiring}.py                  :8082
├── analytics/   app/
│   ├── data/{catalog_repo,scans_repo,ranking_repo}.py
│   ├── api/{routes,internal_routes}.py
│   └── {main,serve,service,rebuilder,wiring}.py                    :8083
├── db/{checkout,inventory,analytics}/schema.sql, seed.py, check_invariant.py
├── scripts/{reset_db,start_all,stop_all,check_invariant}.sh
├── tests/test_boundaries.py       no service touches another's data
├── reports/                       the two submitted runs
├── docker-compose.yml             three Postgres instances
└── pyproject.toml                 uv workspace, five members
```

See [QUALITY-ATTRIBUTES.md](QUALITY-ATTRIBUTES.md) for what this architecture bought and what it cost.

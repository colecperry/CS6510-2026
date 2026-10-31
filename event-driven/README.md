# Self-Checkout, Event-Driven (Week 7)

The same seven endpoints, the same load client, the seventh architecture.

This week the services stop calling each other entirely.
Everything that used to be a request from one service to another is now a message handed to a broker, and the service that cares subscribes to it.
The edge also changes: nginx replaces the hand-written Python gateway, with the gateway kept so the two can be measured against each other.

See [ARCHITECTURE.md](ARCHITECTURE.md) for the diagram and how the pieces fit together.
Built on [week 6's microservices](../microservices), copied and verified identical with `diff -r` before anything changed, so every difference below is deliberate.

## What changed

**Services no longer hold each other's addresses.**
Week 6's checkout held `INVENTORY_URL` and `ANALYTICS_URL` and called both during a payment.
This week there is no such setting in any service, and `tests/test_boundaries.py` reads the source to prove it.

**The payment saga is gone.**
Paying is one transaction against checkout's own database: the basket flips to `COMPLETED` and the outgoing message is written into an `outbox` table together.
A background relay delivers that message afterwards.
Four files were deleted and nothing replaced them.

**The edge is a choice.**
`./scripts/start_all.sh --edge=nginx` or `--edge=python`, listening on 8080 either way.

## Results

Same client, same parameters, same machine as the previous weeks.
Reports in `reports/` (graded, nginx edge) and `reports/comparison/` (Python gateway edge).

Two things changed this week, the communication style and the edge, which one pair of runs could not separate.
So there are four runs: both parameter sets against each edge.
The nginx pair against week 6 shows the whole change; the Python-gateway pair against week 6 shows the events alone.

### Default (10 stations, 60s)

|                  | monolith | layered | pipeline | service-based | microservices | event-driven (python) | event-driven (nginx) |
| ---------------- | -------- | ------- | -------- | ------------- | ------------- | --------------------- | -------------------- |
| Transactions/sec | 98.3     | 99.7    | 123.6    | 45.1          | 41.0          | 37.8                  | **98.7**             |
| Items/sec        | 1,032.6  | 1,048.2 | 1,300.9  | 471.1         | 428.2         | 406.2                 | **1,047.8**          |
| START p95        | 3.79ms   | 3.67ms  | 4.58ms   | 29.05ms       | 29.30ms       | 32.50ms               | **6.58ms**           |
| SCAN p95         | 9.68ms   | 9.49ms  | 7.10ms   | 28.30ms       | 29.96ms       | 33.06ms               | **10.72ms**          |
| COMPLETE p95     | 21.48ms  | 20.95ms | 31.95ms  | 30.58ms       | 47.05ms       | 31.82ms               | **10.19ms**          |
| Error rate       | 0.00%    | 0.00%   | 0.00%    | 0.00%         | 0.00%         | 0.00%                 | **0.00%**            |

### Stress (100 stations, 120s)

|                  | monolith   | layered    | pipeline   | service-based | microservices | event-driven (python) | event-driven (nginx) |
| ---------------- | ---------- | ---------- | ---------- | ------------- | ------------- | --------------------- | -------------------- |
| Transactions/sec | 117.0      | 115.8      | 124.5      | 37.6          | 27.8          | 39.3                  | **168.2**            |
| Items/sec        | 1,222.2    | 1,213.4    | 1,313.7    | 397.4         | 290.0         | 412.8                 | **1,767.1**          |
| START p95        | 6.59ms     | 7.13ms     | 7.07ms     | 588.57ms      | 936.01ms      | 515.92ms              | **41.74ms**          |
| SCAN p95         | 18.39ms    | 19.37ms    | 10.88ms    | 490.93ms      | 749.83ms      | 423.70ms              | **61.26ms**          |
| COMPLETE p95     | 2,722.91ms | 2,712.42ms | 2,726.74ms | 486.44ms      | 734.76ms      | 439.17ms              | **67.01ms**          |
| Error rate       | 0.00%      | 0.02%      | 0.02%      | 0.00%         | 0.00%         | 0.00%                 | **0.00%**            |

### Reading these numbers

**This is the fastest week in the series under stress**, at 168.2 transactions and 1,767.1 items per second, past the pipeline's 124.5 and 1,313.7.
It is also the first week since the split to match the single-process weeks at default load.

**Almost all of that is the edge, not the events.**
The two event-driven columns differ only in which process sits on port 8080.
Swapping the Python gateway for nginx took default throughput from 37.8 to 98.7 transactions per second, and stress throughput from 39.3 to 168.2.
The measured reason is in the CPU table below: the gateway spent 86.5% of a core at default load and 94.7% under stress, while nginx spent 8.9% and 11.6% doing the same job.

Weeks 5 and 6 both named the gateway as the suspected bottleneck without being able to prove it, because there was nothing to compare it against.
This week there is, and it was.

**What the events alone bought** is the Python-gateway column against week 6, which holds the edge fixed.
At default load throughput fell 7.8%, from 41.0 to 37.8, and completion got 32% faster, from 47.05ms to 31.82ms.
Under stress throughput rose 41%, from 27.8 to 39.3, and every latency roughly halved.

That shape is the point.
A payment used to be two database commits in different services with a blocking HTTP call between them.
It is now one commit, and the shopper's request ends there.
Under stress, where week 6's synchronous call made every station wait on inventory, removing the wait is worth more than the broker costs.

**Scanning stayed where it has been since week 4.**
10.72ms p95 against the pipeline's 7.10ms, through two extra network hops and a broker.
The scan path has not changed shape since week 4: queue it in memory, batch it, send it in the background, never make the shopper wait.

**Checkout is now the ceiling.**
With nginx at the edge, checkout sits at 99.5% of a core under stress while everything else is nearly idle.
That is the next thing that would have to be fixed, and it would be fixed by running two copies of checkout, which this architecture allows and week 6's did not in the same way.

| CPU during the run, percent of one core | edge      | checkout  | inventory | analytics |
| --------------------------------------- | --------- | --------- | --------- | --------- |
| nginx, default                          | **8.9%**  | 87.9%     | 15.4%     | 33.8%     |
| nginx, stress                           | **11.6%** | **99.5%** | 4.8%      | 14.8%     |
| Python gateway, default                 | **86.5%** | 53.1%     | 5.5%      | 20.8%     |
| Python gateway, stress                  | **94.7%** | 52.0%     | 5.1%      | 11.5%     |

Measured with `./scripts/measure_cpu.sh`, which samples each process's cumulative CPU time across the run.

**The one regression: under saturation, stock lags by seconds rather than milliseconds.**
At default load the outbox was empty the moment the run ended.
Under stress it took **48 seconds** after the run to drain.
The relay shares an event loop with checkout's request handlers, and at 99.5% of a core it gets starved.
See QUALITY-ATTRIBUTES for why this is acceptable here and what would fix it.

### Correctness

Checked after all four runs with `./scripts/check_invariant.sh`, which waits for the outbox to drain before comparing anything.

| Run                     | SKUs exact | Explained by selling out | Unexplained | Sales announced / applied |
| ----------------------- | ---------- | ------------------------ | ----------- | ------------------------- |
| nginx, default          | 2,000      | 0                        | **0**       | 5,936 / 5,936             |
| nginx, stress           | 1,998      | 2                        | **0**       | 20,254 / 20,254           |
| Python gateway, default | 2,000      | 0                        | **0**       | 2,278 / 2,278             |
| Python gateway, stress  | 2,000      | 0                        | **0**       | 4,787 / 4,787             |

Zero errors across all four runs, 417,021 operations in total.

The two SKUs "explained by selling out" are items that ran past 10,000 units; the contract defines no out-of-stock response, so stock floors at zero.
Every previous week recorded the same case.

### Resilience

The point of the broker, measured rather than asserted.
Full method and figures in [reports/resilience-scenarios.md](reports/resilience-scenarios.md).

| Scenario          | Shopper affected? | Work lost |
| ----------------- | ----------------- | --------- |
| Inventory stopped | No, 20/20 paid    | None      |
| Broker stopped    | No, 20/20 paid    | None      |
| Analytics stopped | No, latency flat  | None      |

The analytics result is new.
Week 6 dropped scans published while analytics was down; this week all 7,120 of them waited in the queue and arrived afterwards.

## Requirements

- Docker, for three Postgres containers and RabbitMQ
- [`uv`](https://docs.astral.sh/uv/)
- **nginx**, installed natively. Tested against 1.31.8 from Homebrew: `brew install nginx`
- JDK 21+ for the load client, and it must be a **native arm64 build** on Apple Silicon. The client uses virtual threads so 17 will not run it, and the x86 Temurin 21 that earlier weeks named will not start on this machine at all. `brew install openjdk@21` works.

nginx runs natively rather than in a container on purpose.
A container on macOS reaches the host through a network translation layer that the Python gateway, a host process on loopback, does not pay.
Putting one edge behind that and not the other would measure Docker instead of the edge.
This costs the week a little self-containment and is recorded as a deviation in QUALITY-ATTRIBUTES.

## Setup

Self-contained: its own databases and its own broker, nothing shared with the other weeks.

```bash
docker compose up -d        # Postgres on 5440, 5441, 5442; RabbitMQ on 5672, UI on 15672
uv sync --all-packages      # a workspace syncs only the root without the flag
cp .env.example .env
./scripts/reset_db.sh       # all three stores, 2000 SKUs, 10,000 units each, and both queues emptied
```

Ports 5440 to 5442 because 5433 through 5439 belong to the earlier weeks, so all seven can run at once.

The broker's management UI is at <http://localhost:15672> with guest/guest.
Both queues are visible there while traffic runs, which is the quickest way to see the system working.

## Running

```bash
./scripts/start_all.sh --edge=nginx     # or --edge=python
./scripts/stop_all.sh
```

It refuses to start if anything already holds 8080, because a run with the wrong edge measures the wrong thing.

Each service is independently deployable and can be started on its own:

```bash
uv run --directory inventory python -m app.serve
```

Use these launchers rather than starting `hypercorn` directly.
The Java load client asks to switch to a newer version of HTTP on every request, and accepting makes the server collapse under sustained load.
Each service's `serve.py` refuses the switch, and week 1's README documents the full investigation.

### A single service, stopped and started

```bash
./scripts/stop_one.sh inventory
./scripts/stop_one.sh inventory --start
```

## Tests

```bash
uv run pytest                              # cross-service boundaries and broker topology
cd checkout  && uv run pytest              # checkout, including the outbox and the relay
cd inventory && uv run pytest              # inventory, including idempotency
cd analytics && uv run pytest              # the ranking rebuilder
```

47 tests. Most need no database, no broker and no web server.

Some do need the real thing, deliberately.
The outbox's guarantee is a Postgres transaction and idempotency is a Postgres `ON CONFLICT` clause, so a stand-in would only be testing the stand-in.
The topology tests read the broker's own description of itself through its management API.
All of them skip with a message when what they need is not running.

**Do not run the test suites while a load run is in progress.**
The inventory tests move real stock, and four of their units once turned up as an invariant violation in a benchmark that was otherwise clean.

**There is no linter configured for this project**, so "run the linter" is not something that can be done here.

## Reproducing the load tests

Stop the other weeks' containers first, so the databases are not sharing CPU:

```bash
cd ../monolith && docker compose down
cd ../layered && docker compose down
cd ../pipeline && docker compose down
cd ../service-based && docker compose down
cd ../microservices && docker compose down
cd ../event-driven && docker compose up -d
```

All four runs, with a reset and a restart between each:

```bash
./scripts/run_all_benchmarks.sh
```

Or one pair at a time: `--only=nginx` or `--only=python`.

By hand, one run:

```bash
./scripts/stop_all.sh && ./scripts/reset_db.sh && ./scripts/start_all.sh --edge=nginx

cd ../load-client
export JAVA_HOME=/opt/homebrew/opt/openjdk@21
export PATH="$JAVA_HOME/bin:$PATH"
./run.sh --baseUrl=http://localhost:8080 --stations=10 --duration=60 --reportDir=../event-driven/reports
```

### Checking correctness after a run

```bash
./scripts/check_invariant.sh
```

It waits for checkout's outbox to empty before comparing, because stock catching up is the normal state of this system rather than a fault.

### Exercising the failure paths

```bash
./scripts/run_scenarios.sh        # all three outages, start to finish
./scripts/measure_lag.sh 10       # how long stock takes to catch up
./scripts/smoke_test.sh           # seven endpoints, six refusals, through whichever edge is up
```

### What bounds the backlog

Two places hold undelivered work.

Scans waiting to leave checkout sit in an in-memory queue capped at 50,000 (`MAX_PENDING` in `checkout/app/ingest.py`).
Above that, new scans are counted and dropped rather than blocking a sale.
At the stress run's scan rate that is about 28 seconds of total broker unavailability before the first scan is shed.

Sales waiting in the broker have no queue-level limit.
RabbitMQ stops accepting publishes when free disk falls below `disk_free_limit`, 50 MB on this broker against 971 GB free.
A sale message is a few hundred bytes, so the practical bound is the disk and is not one this system would reach.

## Project layout

```
event-driven/
├── common/common/bus.py        how every service reaches the broker, and nothing about what they say
├── checkout/   8081   app/bus/publisher.py, app/outbox.py, app/data/outbox_repo.py
├── inventory/  8082   app/bus/consumer.py, app/stock_mover.py
├── analytics/  8083   app/bus/consumer.py, app/rebuilder.py
├── edge/
│   ├── gateway/       the Python reverse proxy, carried from week 5
│   ├── nginx.conf     the same routing table as nginx configuration
│   └── proxy.conf     the proxy settings every route shares
├── db/                three schemas, one seed, the invariant check
├── scripts/           start, stop, reset, smoke, scenarios, benchmarks, lag, CPU
├── reports/           the two graded runs, plus the resilience write-up
│   └── comparison/    the same two runs against the Python gateway
├── ARCHITECTURE.md    the diagram and how a purchase works
└── QUALITY-ATTRIBUTES.md
```

## Submission

<https://github.com/colecperry/CS6510-2026/tree/main/event-driven>

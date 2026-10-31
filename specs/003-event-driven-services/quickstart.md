# Quickstart: Event-Driven Self-Checkout

How to stand the week up, prove it works, and produce the four reports.
Every command runs from `event-driven/` unless it says otherwise.

---

## Prerequisites

- Docker Desktop running, with the other weeks' containers stopped.
- Python 3.12 and `uv`.
- Java 21 for the load client: `export JAVA_HOME=/Library/Java/JavaVirtualMachines/temurin-21.jdk/Contents/Home`
- **nginx, installed natively**: `brew install nginx`. Not a container, for the reason in [research.md](./research.md) D9. If nginx runs inside Docker Desktop's virtual machine it pays a network hop the Python gateway does not, and the comparison the teacher asked for stops meaning anything.

---

## Stand it up

```bash
# Stop every earlier week so nothing competes for CPU.
for w in monolith layered pipeline service-based microservices; do
  (cd ../$w && docker compose down)
done

docker compose up -d          # three Postgres on 5440 to 5442, RabbitMQ on 5672
./scripts/reset_db.sh         # schema and 2,000 SKUs at 10,000 units each, per store
uv sync
./scripts/start_all.sh        # defaults to the nginx edge on 8080
```

`start_all.sh` takes `--edge=nginx` or `--edge=python`, and starts exactly one.
Running both at once is not supported, because they bind the same port and because a run with both is a run that measures neither.

---

## Prove it is working

### The broker is actually carrying the traffic

Open the management UI at <http://localhost:15672> (guest / guest).
Drive a few transactions, then confirm both queues show messages in and out.

This is also the screenshot worth keeping for the architecture diagram, since US2 asks for evidence that every cross-service exchange appears in the broker.

### The seven endpoints behave as before

```bash
./scripts/smoke_test.sh
```

Every response shape and status code must match week 6.
Compare against `../microservices/` if anything looks different; a divergence here is a Principle I violation, not a curiosity.

### No service can reach another

```bash
uv run pytest tests/test_boundaries.py -v
```

Five checks, carried over from week 6 and extended: no service imports another's package, no service's configuration holds another's address, and no service's database credentials reach another's store.

Worth confirming the test is not vacuous by planting a violation and watching it fail, as week 6 did.

---

## Prove the thing the week is actually about

These three are the week's claims. Each is a separate scenario, and each should be run before the benchmark rather than after.

### 1. A payment succeeds while inventory is down

```bash
./scripts/stop_one.sh inventory
# pay for a basket through the edge: it must return 200 with a full receipt
./scripts/stop_one.sh --start inventory
# wait, then:
./scripts/check_invariant.sh
```

**Expected**: the payment returns a receipt immediately. Stock is briefly stale. Once inventory restarts, it drains the queue and the invariant holds exactly.

Week 6 refused this payment after two seconds. That difference is the headline of the week.

### 2. A payment succeeds while the broker is down

```bash
docker compose stop rabbitmq
# pay for a basket: it must still return 200
docker compose start rabbitmq
./scripts/check_invariant.sh
```

**Expected**: the payment returns a receipt, because the outbox row committed with it. The relay delivers once the broker is back.

This is the test that proves the outbox is doing its job. If a payment fails here, something is publishing inside the request path and the design has been built wrong.

### 3. Scans survive analytics being down

```bash
./scripts/stop_one.sh analytics
# scan items for 30 seconds
./scripts/stop_one.sh --start analytics
# wait for the ranking to rebuild, then check it reflects those scans
```

**Expected**: scanning stays as fast as before, and the scans arrive once analytics returns.

Note the difference from week 6, where these scans were dropped and never recovered. Here they sit in a durable queue. The bound on how many may accumulate before shedding is stated in the README.

### 4. Measure how long stock stays stale

```bash
./scripts/measure_lag.sh
```

Pays for a basket, then polls until the stock change lands, and reports the delay.
FR-011 requires this number to be stated rather than estimated, and SC-003 requires it measured.

---

## The four benchmark runs

Two edges, two parameter sets. Reset the database between every run.

```bash
cd ../load-client
export JAVA_HOME=/Library/Java/JavaVirtualMachines/temurin-21.jdk/Contents/Home
export PATH="$JAVA_HOME/bin:$PATH"
```

**Graded pair, nginx edge**, into `event-driven/reports/`:

```bash
./run.sh --baseUrl=http://localhost:8080 --stations=10  --duration=60  --reportDir=../event-driven/reports
# reset and restart between runs
./run.sh --baseUrl=http://localhost:8080 --stations=100 --duration=120 --reportDir=../event-driven/reports
```

**Comparison pair, Python gateway edge**, into `event-driven/reports/comparison/`:

```bash
# restart with ./scripts/start_all.sh --edge=python first
./run.sh --baseUrl=http://localhost:8080 --stations=10  --duration=60  --reportDir=../event-driven/reports/comparison
./run.sh --baseUrl=http://localhost:8080 --stations=100 --duration=120 --reportDir=../event-driven/reports/comparison
```

`./scripts/run_all_benchmarks.sh` does all four with the resets in between, which is less error-prone than doing it by hand.

**After every single run**, before touching anything:

```bash
./scripts/check_invariant.sh
```

Wait for the outbox to drain first. The script should refuse to report until it does, rather than reporting a violation that is really just a message in flight.

---

## Reading the results

Two numbers come out of four runs, and they answer two different questions.

| Comparison                                  | Question answered                                                                                        |
| ------------------------------------------- | -------------------------------------------------------------------------------------------------------- |
| nginx versus Python gateway, both this week | Does swapping the edge help? This is what the teacher asked.                                             |
| Python gateway, this week versus week 6     | What did moving to events cost or buy? The edge is held constant, so the difference is the architecture. |

Quoting a figure into the README or QUALITY-ATTRIBUTES means reading it out of the JSON, not remembering it.
Week 6 shipped a script that checks every quoted number against the reports; carry it forward and run it before committing.

---

## What to expect

Written down now so a surprise is visible as a surprise.

- **Payment latency should fall**, possibly a lot. It no longer waits on a second service, which in week 6 was a network round trip plus a commit.
- **Throughput should rise** for the same reason, since the load client is closed-loop and throughput is stations divided by time per transaction.
- **Scan latency should be unchanged**, because only the destination of the batch changed.
- **nginx should lower edge CPU substantially.** Week 5 measured the Python gateway at 87% of a core against checkout's 53%.
- **Stock lag should be small**, tens of milliseconds, dominated by how often the relay polls.

A result that contradicts any of these is the interesting one and belongs in the write-up with its magnitude, not an explanation.

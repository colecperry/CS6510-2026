# Quickstart and Validation

How to run this week's system and prove it does what the spec claims.
Scenarios are ordered so that each one only depends on the ones above it.

## Prerequisites

- Docker, for the three Postgres containers
- [`uv`](https://docs.astral.sh/uv/)
- JDK 21+, only to build and run the load client

## Setup

```bash
cd microservices
docker compose up -d          # Postgres on 5437, 5438, 5439
uv sync --all-packages        # a workspace syncs only the root without this
cp .env.example .env
./scripts/reset_db.sh         # creates and seeds all three stores
```

`reset_db.sh` must seed the catalogue identically into all three stores.
If the copies ever disagree, a receipt could charge a different price from the one the ranking reports, so the reset script checks the row counts match before it exits.

## Running

```bash
./scripts/start_all.sh        # gateway 8080, checkout 8081, inventory 8082, analytics 8083
```

Each service can also be started alone, which is the point of the week:

```bash
uv run --directory inventory python -m app.serve
```

## Validation scenarios

### 1. The shopper journey works (US1)

Through the gateway only. Start a basket, scan into it, pay, and confirm the receipt is itemised and the total matches.
Then confirm the four refusal paths: unknown SKU, missing basket, scanning a paid basket, paying an empty basket.

Expected: identical responses to week 5, byte for byte in shape.

### 2. Each service owns its own store (US2)

Stop everything, then start one service at a time and confirm it answers its own paths.

Then the structural check:

```bash
uv run pytest tests/test_boundaries.py
```

This parses the source and asserts no service holds a query against a table it does not own, and that no service holds credentials for a store it does not own.
It must be verified non-vacuous by planting a violation and watching it fail, as week 5's equivalent was.

Expected: inventory alone answers `/items` and `/inventory/low-stock`; checkout alone answers the four transaction paths; stopping analytics does not stop a shopper paying.

### 3. A payment spans two stores and stays correct (US3)

The ordinary path first: pay for a basket, then read the quantity from the inventory store directly and confirm it fell by exactly what was bought.

Then the failure paths, which are the actual assignment:

**Stock step fails.** Stop inventory, then attempt a payment.
Expected: the payment is refused within the retry budget rather than hanging, the basket is left open and scannable, and inventory's stock is untouched when it comes back.

**Duplicate delivery.** Call `POST /internal/stock/reduce` twice with the same `idempotencyKey`.
Expected: stock falls once. The second call returns `alreadyApplied: true`.

**Interrupted mid-saga.** Kill checkout between the stock reduction and the basket being marked paid.
Expected: on restart, recovery runs before checkout accepts traffic, finds the `payment_saga` row, asks inventory whether it was applied, and drives it to an end state. The invariant holds afterwards.

**Concurrent purchase of the last unit.** Two payments for the final unit of one SKU at the same moment.
Expected: stock does not go negative and neither reduction is lost.

### 4. Scans reach reporting across a boundary (US4)

Scan more than the rebuild interval of 500 items, then request the ranking.

Expected: the ranking appears, SKU-000001 first with a Zipf-shaped tail, matching previous weeks.
Scanning never waits for analytics: stop analytics mid-run and confirm scan latency is unaffected and the ranking catches up on restart.

### 5. The results reproduce (US5)

Build the client once:

```bash
cd ../load-client
export JAVA_HOME=/opt/homebrew/opt/openjdk@21/libexec/openjdk.jdk/Contents/Home
export PATH="$JAVA_HOME/bin:$PATH"
./build.sh
```

Stop the other weeks' containers first, so the databases are not sharing CPU.
Reset and restart before **each** run, or the two are not comparable.

```bash
./run.sh --baseUrl=http://localhost:8080 --stations=10  --duration=60  --reportDir=../microservices/reports
./run.sh --baseUrl=http://localhost:8080 --stations=100 --duration=120 --reportDir=../microservices/reports
```

### 6. The invariant holds

```bash
./scripts/check_invariant.sh
```

Units sold now live in the checkout store and quantities in the inventory store, so this reads both and compares them outside the database.
It reports the same four counts as previous weeks: negative stock, exact matches, discrepancies explained by selling out, and unexplained violations.

`unexplained_violations` must be zero.

It must also report how many sagas were compensated during the run, including zero, as FR-027 requires.

## What to expect in the numbers

Written down in advance so a surprise is recognisable as one.

Throughput should stay near week 5's 45 tx/sec, because the gateway was measured at 87 to 92 percent of a CPU core and is the cap, not checkout.
COMPLETE p95 should rise by roughly one HTTP round trip plus a second database commit.
SCAN p95 should be close to week 5's, because the scan path gained no synchronous work.

If throughput falls well below 45, something other than the gateway has become the constraint and the CPU measurement from week 5 should be repeated before drawing any conclusion.

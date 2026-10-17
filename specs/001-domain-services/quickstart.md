# Quickstart: Service-Based Self-Checkout

**Date**: 2026-10-13
**Feature**: [spec.md](spec.md)

How to run the system and prove it does what the spec says.
Each section maps to a success criterion, so working through it top to bottom validates the feature.

---

## Prerequisites

- Docker, for the database
- `uv`
- A JDK 21 or later, only to build and run the load client

---

## Start it

```bash
cd service-based
docker compose up -d        # the one shared database
uv sync
cp .env.example .env
./scripts/reset_db.sh       # 2,000 items, 10,000 units each
./scripts/start_all.sh      # entry point and the three services
```

Four processes come up. The entry point is the only one the load client ever talks to.

To stop:

```bash
./scripts/stop_all.sh
docker compose down
```

---

## Validate

### Every endpoint works through the entry point (SC-002)

Open a basket, scan into it, pay, and read both reports.
Each call goes to the single address; each is answered by a different service without the caller knowing.

Expected: a basket is created, scans return the item's name and price with updated totals, paying returns an itemised receipt, and both report endpoints answer.

### The refusals are correct

Scan an unknown barcode, scan into a paid basket, pay twice, pay for an empty basket, open a basket with no station.

Expected: each is refused with the same code and message the previous weeks produced. See [contracts/README.md](contracts/README.md).

### Each service stands alone (SC-005)

Stop everything, then start one service by itself and call it directly rather than through the entry point.

Expected: it answers the paths it owns. The table in [contracts/README.md](contracts/README.md) says which those are for each service.
If a service needs another one running in order to answer its own requests, the boundary is wrong.

### No service calls another (SC-006)

Inspect each service for the address of another service, or for an HTTP client capable of reaching one.

Expected: none. Only the entry point knows where the services are.
Worth automating, since this is the rule the assignment is most explicit about.

### A scan reaches the ranking without a service call (User Story 3)

Scan enough items to cross the rebuild threshold, wait briefly for the background rebuild, then ask for the ranking.

Expected: the items appear, most-scanned first.
This is the whole design in one test. The selling service never told the reporting service anything; it wrote to the database and reporting found it.

### Stock is correct after a run (SC-004)

After each load run, check that for every item the units sold equal the reduction in stock held, and that nothing went below zero.
Use the same query the previous weeks use, pointed at this week's database.

Expected: zero unexplained discrepancies.
An item that sold out and stopped at zero is expected and is not a violation, since the contract defines no out-of-stock response.

---

## Measure

Two runs, with the previous weeks' databases stopped so nothing competes for the machine.

Reset the database and restart the services before **each** run, or the two are not comparable.

```bash
cd ../load-client
export JAVA_HOME=/Library/Java/JavaVirtualMachines/temurin-21.jdk/Contents/Home
export PATH="$JAVA_HOME/bin:$PATH"
./build.sh

# default
./run.sh --baseUrl=http://localhost:8080 --stations=10 --duration=60 --reportDir=../service-based/reports

# stress
./run.sh --baseUrl=http://localhost:8080 --stations=100 --duration=120 --reportDir=../service-based/reports
```

Both produce a timestamped report in `service-based/reports/`. Those two files are the deliverable.

### Reading the results

Compare against the previous three weeks.

- **Errors** must be zero at the default load, and no worse than last week under stress. This is the criterion that can fail (SC-002, SC-003).
- **Scan latency** is expected to get worse. Report it and compare it. No figure here fails the week (SC-001).
- **The ranking** should look materially the same as the previous weeks produced, confirming that splitting the system did not change what it reports (SC-007).

---

## Done when

- Both reports exist in `reports/`
- Errors within the limits above, and the stock check clean for both runs
- Each service demonstrated answering alone
- No service found calling another
- The latency comparison written up, including any regression, with hypotheses labelled as hypotheses

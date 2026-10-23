# Implementation Plan: Microservices Self-Checkout

**Branch**: `002-microservices` | **Date**: 2026-10-19 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/002-microservices/spec.md`

## Summary

Week 5 split the system into three services that still shared one database.
This week each service gets its own database, which removes the last thing holding them together and breaks the one mechanism that kept the books correct: the single transaction that marked a basket paid and reduced stock at the same time.

Those two writes now live in different databases, so payment becomes an orchestrated saga.
Checkout drives it: claim the basket, ask inventory to reduce stock, mark the basket paid, return the receipt.
If the stock step fails, checkout undoes the claim and refuses the payment, so the shopper never receives a receipt for stock that was not actually taken.

Scanning crosses a boundary too, but in the other direction and without urgency: checkout batches scans and pushes them to analytics in the background, so a shopper never waits for reporting.

The gateway is unchanged from week 5, including its measured ceiling of 87 to 92 percent of a CPU core.

## Technical Context

**Language/Version**: Python 3.12

**Primary Dependencies**: FastAPI 0.142.1, Starlette 1.6.0, Hypercorn 0.18.0, asyncpg 0.31.0, Pydantic 2.13.5, httpx (now in checkout as well as the gateway). All pinned exactly to week 4's resolved versions, per Principle II.

**Storage**: Three separate PostgreSQL 16 instances, one per service, on host ports 5437 (checkout), 5438 (inventory) and 5439 (analytics). Raw SQL via asyncpg, no ORM.

**Testing**: pytest with pytest-asyncio, using stand-in repositories and a stand-in inventory client so the saga can be tested without a database or a network.

**Target Platform**: Four local processes plus three containers on one machine.

**Project Type**: Distributed web service behind a routing entry point.

**Performance Goals**: None to pass. The cost of database-per-service is the finding. Written down in advance so an unexpected result is visible as unexpected: throughput stays near week 5's 45 tx/sec because the gateway, not checkout, is the cap; COMPLETE p95 rises by roughly the cost of one extra HTTP round trip plus a second database commit.

**Constraints**: The seven endpoints, their request and response shapes, and their status codes cannot change. The load client cannot be modified. Load parameters stay 10 stations for 60s and 100 stations for 120s.

**Scale/Scope**: 2,000 SKUs at 10,000 units each. Roughly 1,300 scans per second at peak.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle                                  | Gate                                                                                            | Verdict    |
|--------------------------------------------|-------------------------------------------------------------------------------------------------|------------|
| I. The Contract Is Fixed                   | Seven endpoints unchanged; `status` stays within `OPEN, COMPLETED, CANCELLED`; client untouched | **PASS**   |
| II. Weeks Must Stay Comparable             | Own folder, own containers and ports, versions pinned identically, same load parameters         | **PASS**\* |
| III. Correctness Before Speed              | Invariant holds when payment returns; every guard folded into its mutating statement            | **PASS**   |
| IV. Evidence, Not Assertion                | Two reports planned; invariant now checked across three databases; regressions reported         | **PASS**   |
| V. Readable By Someone Who Has Not Seen It | File headers, two-line comments, README and QUALITY-ATTRIBUTES planned                          | **PASS**   |

\* Two deviations in letter, both recorded under Complexity Tracking: this week takes three database containers and three ports rather than one, and `db/schema.sql` splits into three files. The constitution anticipates the second ("unless a week's architecture genuinely requires a change") and requires it to be documented, which it is.

**Principle I, the one that nearly broke.**
The contract pins the transaction status to three values, so the saga's in-flight state cannot be a fourth.
A basket mid-payment therefore keeps the status `OPEN` on the wire, and the saga's progress is held in a separate table and a non-serialised column.
Checked against `spec/self-checkout-openapi.yaml:284`.

**Principle III, the one worth watching.**
Claiming a basket for payment must exclude a second concurrent payment and must also stop a scan landing after the receipt lines were computed.
Both are folded into one conditional `UPDATE`, so there is no read-then-write anywhere on the path.

**Post-design re-check.**
After Phase 1 the verdicts are unchanged.
The design added two tables and three internal endpoints, none of which appear on the public contract, and the gateway routes nothing under `/internal`, so Principle I is not touched by them.

## Project Structure

### Documentation (this feature)

```text
specs/002-microservices/
├── plan.md              # This file (/speckit-plan command output)
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
├── contracts/           # Phase 1 output
└── tasks.md             # Phase 2 output (/speckit-tasks)
```

### Source Code (repository root)

```text
microservices/
├── common/                       shared CONTRACT only, no shared data access
│   └── common/
│       ├── config.py             settings from .env
│       ├── errors.py             domain errors, no HTTP
│       ├── schemas.py            the wire contract, identical across services
│       ├── error_handlers.py     domain error -> status code
│       └── pool.py               how to open a pool, not what to query
├── gateway/     app/{main,serve,routes}.py                        :8080
├── checkout/    app/{main,serve,service,ingest,wiring}.py          :8081
│                app/api/routes.py
│                app/clients/{inventory_client,analytics_client}.py outbound calls
│                app/saga/{coordinator,recovery}.py                 the orchestration
│                app/data/{catalog_repo,baskets_repo,saga_repo}.py
├── inventory/   app/{main,serve,service,wiring}.py                 :8082
│                app/api/{routes,internal_routes}.py
│                app/data/{catalog_repo,stock_repo,applied_requests_repo}.py
├── analytics/   app/{main,serve,service,rebuilder,wiring}.py       :8083
│                app/api/{routes,internal_routes}.py
│                app/data/{scans_repo,ranking_repo}.py
├── db/
│   ├── checkout/schema.sql
│   ├── inventory/schema.sql
│   ├── analytics/schema.sql
│   └── seed.py                   seeds all three, catalogue replicated identically
├── scripts/{reset_db.sh, start_all.sh, stop_all.sh, check_invariant.sh}
├── tests/test_boundaries.py      no service touches another's database
├── reports/
├── docker-compose.yml            three Postgres services on 5437, 5438, 5439
└── pyproject.toml                uv workspace, five members
```

**Structure Decision**: The week 5 layout is kept so the two can be compared directly, with one deliberate change.

In week 5, `common/` held every repository, because the brief said coupling should happen at the database access layer.
That is exactly what must not happen now.
If `common` still exported a `stock_repo`, checkout could query inventory's tables by importing it, and FR-004 would hold only by good manners.

So `common/` shrinks to the things that genuinely must agree across services: the wire contract, the domain errors, the error-to-status mapping, settings, and the mechanics of opening a connection pool.
Every SQL statement moves into the service that owns the table.
Two services never hold a query against the same table, which is what makes `tests/test_boundaries.py` able to check the rule mechanically rather than by inspection.

## Complexity Tracking

| Violation                                                                                | Why Needed                                                                                                                            | Simpler Alternative Rejected Because                                                                                                                                                                                       |
|------------------------------------------------------------------------------------------|---------------------------------------------------------------------------------------------------------------------------------------|----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| Three database containers and three host ports, where the constitution says one per week | The assignment requires each microservice to own its data store; one container cannot give three services genuinely separate stores   | Three schemas inside one Postgres would share a process, a page cache and a connection limit, so "its own data store" would be a naming convention rather than a boundary, and a stray cross-schema query would still work |
| `db/schema.sql` splits into three files                                                  | Each store holds only the tables its owner uses; there is no longer a single schema to keep byte-identical                            | Keeping one combined schema and applying it to all three would give every service every table, which is the thing being removed                                                                                            |
| The catalogue table exists in all three stores                                           | Checkout needs name and price for receipts, analytics needs names for the ranking, and neither may read inventory's store             | A synchronous lookup to inventory on every scan would put a network call on the most frequent operation in the system, which runs about 1,300 times a second                                                               |
| Services now call each other over HTTP, which week 5 forbade outright                    | Separate stores leave no shared table to coordinate through, and the assignment explicitly asks for coordination across microservices | There is no alternative once the stores are split; this is the change the assignment is testing                                                                                                                            |

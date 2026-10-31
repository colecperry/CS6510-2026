# Implementation Plan: Event-Driven Self-Checkout

**Branch**: `003-event-driven-services` | **Date**: 2026-10-30 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `specs/003-event-driven-services/spec.md`

## Summary

Week 6 split the system into three services with a database each, but they still spoke over direct HTTP calls, so a payment could not proceed unless the inventory service answered within two seconds.
This week those calls become messages through a broker.

The headline change is that paying no longer waits for stock.
Checkout marks the basket paid and records the outgoing message in the same transaction, returns the receipt, and a relay delivers the message when the broker is reachable.
Inventory reduces stock when it arrives.
Stock becomes eventually exact rather than exact at the instant of sale, which is a deliberate amendment to Principle III and the thing the week is really measuring.

Three consequences fall out of that one decision, and each removes code rather than adding it.
The saga table, its compensation path and its startup recovery all disappear, because a durable message the broker will deliver replaces the thing they existed to repair.
The `paying_since` lock disappears, because payment is now a single transaction in a single database and the status guard already does its job.
The question checkout asked inventory at startup disappears, because there is nothing left to ask.

Alongside this, nginx replaces the hand-written gateway at the edge, and four load runs are performed instead of two so the edge change and the communication change can be told apart.

## Technical Context

**Language/Version**: Python 3.12, managed with `uv` workspaces, as in weeks 2 through 6.

**Primary Dependencies**: FastAPI 0.142.1, Starlette 1.6.0, Hypercorn 0.18.0, asyncpg 0.31.0, Pydantic 2.13.5, all pinned exactly to week 4's resolved versions. New this week: `aio-pika` for the broker, and nginx at the edge.

**Message Broker**: RabbitMQ 4.x in its own container. See [research.md](./research.md) D1.

**Storage**: Three Postgres 16 containers, one per service, on host ports 5440, 5441 and 5442. Ports 5433 through 5439 belong to earlier weeks.

**Testing**: pytest with pytest-asyncio, per service, plus a cross-service boundary test at the workspace root.

**Target Platform**: macOS host, services as host processes, databases and broker in Docker.

**Project Type**: Distributed web service. Three business services, one edge, one broker, three databases.

**Performance Goals**: Scan p95 no slower than week 6. Payment p95 faster than week 6, since it no longer waits on another service. Throughput reported against all six previous weeks.

**Constraints**: The seven endpoints and their shapes are fixed. The load client is not modified. Stock must be eventually exact with a measured bound, and must never go negative.

**Scale/Scope**: 2,000 SKUs, 10,000 units each. Default run and a 100 station, 120 second stress run, each performed against both edges.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle                                  | Status  | How                                                                                                                                                                                                                     |
| ------------------------------------------ | ------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| I. The Contract Is Fixed                   | PASS    | The seven endpoints keep their shapes. The load client is untouched. Every internal change sits behind the contract.                                                                                                    |
| II. Weeks Must Stay Comparable             | PASS    | New folder `event-driven/`, three new Postgres ports, dependency versions pinned to week 4's. The week changes two things, so four runs are performed to attribute each.                                                |
| III. Correctness Before Speed              | AMENDED | Stock becomes eventually exact rather than exact at the instant of sale. Recorded in Complexity Tracking below and in the week's analysis. The invariant itself is unchanged and is checked once messages have drained. |
| IV. Evidence, Not Assertion                | PASS    | Four reports committed, figures checked mechanically against them, verified and assumed stated separately.                                                                                                              |
| V. Readable By Someone Who Has Not Seen It | PASS    | File headers, two line comments, README and QUALITY-ATTRIBUTES, no em dash.                                                                                                                                             |

**Gate result**: proceed. One amendment, justified below, recorded rather than silent, which is what the Governance section requires.

### Re-check after Phase 1 design

Two things the design surfaced that the gate above did not.

**The schema changes again, and the Technology And Layout Constraints require that to be justified.**
Checkout drops `paying_since` and the `payment_saga` table and gains an `outbox` table; inventory's `applied_requests` loses `restored_at` and rekeys on a message id.
Every change is a consequence of the one decision in FR-008 and is documented in [data-model.md](./data-model.md) against the week 6 column it replaces.
None of them touches the data the contract returns.

**Native nginx breaks folder self-containment, and this one is a real deviation.**
The constraints require each week's folder to be self-contained, and a `brew install nginx` lives outside it.
The alternative, an nginx container, was rejected in [research.md](./research.md) D9 because on macOS it would pay a network hop the Python gateway does not, making the edge comparison unattributable and breaking Principle II instead.
Self-containment is a convenience principle and comparability is the one the whole repository exists for, so the trade goes this way.
The README must state the dependency and the version, and QUALITY-ATTRIBUTES must record the deviation.

## Project Structure

### Documentation (this feature)

```text
specs/003-event-driven-services/
├── plan.md              # This file
├── research.md          # Phase 0 output: the eleven decisions
├── data-model.md        # Phase 1 output: tables and messages
├── quickstart.md        # Phase 1 output: how to run and verify it
├── contracts/           # Phase 1 output: the message contract
└── tasks.md             # Phase 2 output, created by /speckit-tasks
```

### Source Code (repository root)

```text
event-driven/
├── common/                     shared library, imported by every service
│   └── common/
│       ├── config.py           settings from .env
│       ├── errors.py           domain errors, no HTTP
│       ├── error_handlers.py   domain error to status code
│       ├── schemas.py          the wire contract
│       ├── pool.py             database connection pool
│       └── bus.py              NEW: broker connection, publish, consume
├── edge/
│   ├── nginx.conf              NEW: the reverse proxy config
│   └── gateway/                the week 6 Python gateway, kept for comparison
├── checkout/    app/{main, serve, service, ingest, outbox, api/, data/, bus/}
├── inventory/   app/{main, serve, service, stock_mover, api/, data/, bus/}
├── analytics/   app/{main, serve, service, rebuilder, api/, data/, bus/}
├── db/{checkout,inventory,analytics}/schema.sql, seed.py, check_invariant.py
├── scripts/{reset_db, start_all, stop_all, check_invariant, run_all_benchmarks}.sh
├── reports/                    four JSON reports, two graded and two comparison
├── docker-compose.yml          three Postgres plus RabbitMQ
├── ARCHITECTURE.md             the diagram, worth 1 point
├── README.md
└── QUALITY-ATTRIBUTES.md
```

**Structure Decision**: The week starts as a byte-identical copy of `microservices/`, verified before any change, as Principle II's workflow section requires.
The layout is therefore week 6's, with four differences.

A `bus/` package appears inside each service, holding what that service publishes and what it consumes.
It sits beside `data/` deliberately: both are the service's edges on the outside world, one to its store and one to the broker.

A `bus.py` joins `common/`, holding the connection and the publish and consume mechanics that all three share.
It holds no message definitions, for the same reason `common/` holds no SQL: a shared definition is a shared coupling, and each message belongs to the service that publishes it.

An `edge/` folder holds both the nginx config and week 6's Python gateway, so the two can be swapped without anything else moving.

Three files are expected to be deleted rather than changed: `checkout/app/saga/coordinator.py`, `checkout/app/saga/recovery.py` and `checkout/app/data/saga_repo.py`.
If they survive, that is a finding and the analysis should say why.

## Complexity Tracking

> Filled because the Constitution Check records one amendment.

| Violation                                                                                 | Why Needed                                                                                                                                                                                                                                                                                                                                                                                                | Simpler Alternative Rejected Because                                                                                                                                                                                                                                                                                                             |
| ----------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Principle III amended: stock is eventually exact rather than exact at the instant of sale | The assignment requires all cross-service communication to be events. Holding the receipt until a reply arrives rebuilds a synchronous call out of asynchronous parts, puts broker latency on the critical path, and leaves the broker earning nothing. Releasing the receipt first is what buys the property the week exists to demonstrate: the shop keeps selling while the inventory service is down. | Blocking on a reply message was rejected for the reasons above. A reservation model, decrementing stock when the basket is claimed and releasing it if abandoned, keeps stock exact without blocking but adds a reservation lifecycle, an expiry sweep and a second failure mode, which is more machinery than a five point assignment warrants. |
| Two variables change in one week: the communication style and the edge                    | The teacher asked for nginx specifically, and the course brief asked for events. Both are due in the same submission.                                                                                                                                                                                                                                                                                     | Doing only one was rejected because it ignores half of what was asked. Instead, Principle II is satisfied by measurement rather than by restraint: four runs, two per edge, so each change gets its own number.                                                                                                                                  |

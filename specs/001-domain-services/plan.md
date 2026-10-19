# Implementation Plan: Service-Based Self-Checkout

**Branch**: `spec-kit-adoption` | **Date**: 2026-10-13 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/001-domain-services/spec.md`

## Summary

Split the self-checkout system into three independently deployable domain services sharing one
database, with a thin entry point in front so the load client still sees a single address.
No service calls another; where two need the same information, they coordinate through the database.

Built by copying the pipes-and-filters week and splitting it at the process boundary.
Its batched scan writer stays with selling, keeping scans off the request path; the ranking rebuild
moves to reporting and is driven by polling the database rather than by an in-memory queue.
Query code needed by more than one service moves into a shared library with a single definition.

## Technical Context

**Language/Version**: Python 3.12

**Primary Dependencies**: FastAPI and Hypercorn for each service; asyncpg for database access; one
HTTP client in the entry point for forwarding. Versions pinned to match the previous weeks,
including the exact Starlette pin the constitution requires.

**Storage**: One PostgreSQL 16 instance, shared by all three services. Schema byte-identical to the
previous weeks. Host port 5436, the next unused one.

**Testing**: pytest, with stand-in repositories so the business rules run without a database or a
web server, as in the previous weeks. Plus a check that no service imports another.

**Target Platform**: Four local processes on one machine, plus the database in Docker.

**Project Type**: Distributed web services behind a routing entry point.

**Performance Goals**: None set, deliberately. The spec (SC-001) states that no latency figure can
fail this week. The cost of splitting is the result being measured.

**Constraints**: Zero errors at the default load; no worse than the previous week under stress; the
stock invariant must hold in both runs. Those are the only hard limits.

**Scale/Scope**: 2,000 items, 10 and 100 concurrent stations, roughly 1,300 scans a second at the
heavy load.

### Service naming

The spec describes three business functions. The implementation names them after the URL namespaces
they own, which matches the previous weeks and avoids a second vocabulary.

| Spec calls it | Directory    | Owns                             |
|---------------|--------------|----------------------------------|
| Selling       | `checkout/`  | the four transaction paths       |
| Stock         | `inventory/` | `/items`, `/inventory/low-stock` |
| Reporting     | `analytics/` | `/analytics/popular-items`       |

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-checked after Phase 1 design.*

| Principle                                  | Gate                                                                                           | Status                                                                                                                                                                                                                   |
|--------------------------------------------|------------------------------------------------------------------------------------------------|--------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| I. The Contract Is Fixed                   | Same seven endpoints, same shapes, load client unmodified                                      | **Pass**. FR-001 and FR-002. The entry point forwards without rewriting, so what the client sees is byte-identical.                                                                                                      |
| II. Weeks Must Stay Comparable             | Own folder, own port, finished weeks untouched, versions pinned identically                    | **Pass**. New `service-based/` folder, database on 5436, nothing else edited. One workspace keeps all four processes on one resolved dependency set, so services cannot drift from each other either.                    |
| III. Correctness Before Speed              | Stock invariant holds; guards inside the writing statement; inventory exact, analytics may lag | **Pass**. The payment path moves process but not shape: same single transaction, same guard folded into the UPDATE. Analytics lag increases by the polling interval, which the spec permits and the write-up will state. |
| IV. Evidence, Not Assertion                | Two committed reports; figures checked mechanically; regressions reported                      | **Pass**. FR-019 and FR-020. A latency regression is expected here and the spec forbids fixing it quietly.                                                                                                               |
| V. Readable By Someone Who Has Not Seen It | File headers, self-contained comments, README and QUALITY-ATTRIBUTES, no em dashes             | **Pass**. Same documentation set as the previous weeks.                                                                                                                                                                  |

**Re-check after Phase 1**: still passing. The design introduces no violation, and the Complexity
Tracking table below is empty.

One point worth recording: Principle III says inventory must be exact and analytics may lag, and this
design spends exactly that allowance. Scans reach the ranking through the database on a poll, so the
ranking is behind by up to one polling interval more than before. Stock is untouched by this and
remains exact.

## Project Structure

### Documentation (this feature)

```text
specs/001-domain-services/
├── plan.md              # This file
├── research.md          # Phase 0: seven decisions with alternatives
├── data-model.md        # Phase 1: entities and which service owns each
├── quickstart.md        # Phase 1: how to run and validate
├── contracts/
│   └── README.md        # Phase 1: routing table and service boundaries
├── checklists/
│   └── requirements.md  # spec quality, 16/16
└── tasks.md             # Phase 2, created by /speckit-tasks
```

### Source Code (repository root)

```text
service-based/
├── common/                      shared library, installed by each service
│   ├── pyproject.toml
│   └── common/
│       ├── config.py            settings from .env
│       ├── errors.py            domain errors, no HTTP
│       ├── schemas.py           the wire contract
│       └── data/
│           ├── pool.py          connection handling, size passed in per service
│           ├── catalog_repo.py  used by all three services
│           ├── baskets_repo.py  used by checkout
│           ├── stock_repo.py    written by checkout, read by inventory
│           └── scans_repo.py    written by checkout, read by analytics
├── gateway/                     the entry point, port 8080
│   └── app/{main,serve,routes}.py
├── checkout/                    port 8081
│   └── app/{main,serve,service,ingest}.py, api/
├── inventory/                   port 8082
│   └── app/{main,serve,service}.py, api/
├── analytics/                   port 8083
│   └── app/{main,serve,service,rebuilder}.py, api/
├── db/{schema.sql, seed.py}     unchanged from the previous week
├── scripts/{reset_db.sh, start_all.sh, stop_all.sh}
├── tests/                       business rules, plus the no-cross-imports check
├── reports/                     the two submitted runs
├── docker-compose.yml           Postgres on 5436
└── pyproject.toml               workspace listing the five members
```

**Structure Decision**: Five packages in one workspace. Four are runnable processes; `common/` is a
library none of them runs. Each runnable package has its own `pyproject.toml` and its own entry
point, satisfying FR-004, while the single workspace keeps one resolved dependency set so the
services cannot drift from each other mid-week.

The split of `common/data/` into four repository modules follows the ownership table in
[data-model.md](data-model.md), so a reader can see at a glance which service touches which tables.
`catalog_repo` is imported by all three; `stock_repo` and `scans_repo` are each imported by two, and
those two are precisely the places where coupling happens at the database layer, as the brief
describes.

## Complexity Tracking

> Fill ONLY if Constitution Check has violations that must be justified

No violations. Table intentionally empty.

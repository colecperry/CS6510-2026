# Tasks: Service-Based Self-Checkout

**Input**: Design documents from `specs/001-domain-services/`

**Prerequisites**: [plan.md](plan.md), [spec.md](spec.md), [research.md](research.md), [data-model.md](data-model.md), [contracts/](contracts/README.md)

**Tests**: Included. The constitution's Weekly Workflow requires tests to pass before a week is declared done, and the previous three weeks each ship a unit suite that runs without a database.

**Organization**: Grouped by user story, so each can be built and tested on its own.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: can run in parallel; touches different files and depends on nothing unfinished
- **[Story]**: which user story the task serves
- Paths are relative to the repository root

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Stand up the folder and its database before any code moves.

- [X] T001 Create `service-based/` by copying `pipeline/`, excluding `.venv/`, `__pycache__/`, `.pytest_cache/`, `reports/`, and the three markdown files; verify with `diff -r` that every copied file is byte-identical before changing anything
- [X] T002 Write the workspace root `service-based/pyproject.toml` listing the five members: `common`, `gateway`, `checkout`, `inventory`, `analytics`
- [X] T003 [P] Write `service-based/docker-compose.yml` with `container_name: self-checkout-db-services` and host port `5436`, keeping `max_connections=200` unchanged
- [X] T004 [P] Write `service-based/.env.example` and copy to `.env`: database URL on port 5436, plus the three service addresses the gateway needs
- [X] T005 [P] Copy `pipeline/db/schema.sql` to `service-based/db/schema.sql` byte-identical, and copy `pipeline/scripts/reset_db.sh` preserving its executable bit
- [X] T006 Run `uv sync` in `service-based/` and confirm the resolved runtime dependency versions match `pipeline/uv.lock` exactly

**Checkpoint**: the folder stands up and the database can be created, with no application code changed yet.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: The shared library every service imports. Nothing else can start until this exists.

- [X] T007 Write `service-based/common/pyproject.toml` declaring the library and its dependencies
- [X] T008 [P] Move settings to `service-based/common/common/config.py`, adding the three service addresses and a per-service database pool size
- [X] T009 [P] Move domain errors to `service-based/common/common/errors.py` unchanged
- [X] T010 [P] Move the wire contract to `service-based/common/common/schemas.py` unchanged
- [X] T011 Move connection handling to `service-based/common/common/data/pool.py`, changing `max_size` from a constant to an argument so each service sizes its own pool
- [X] T012 [P] Move the catalogue read to `service-based/common/common/data/catalog_repo.py` unchanged; imported by all three services
- [X] T013 [P] Move basket SQL to `service-based/common/common/data/baskets_repo.py`, keeping the stock decrement inside `complete()`'s single transaction, since FR-012 requires marking paid and reducing stock to happen together
- [X] T014 [P] Create `service-based/common/common/data/stock_repo.py` holding only the low-stock read (`qty` below a threshold, joined to the catalogue, lowest first); written by checkout via T013, read by inventory
- [X] T015 [P] Create `service-based/common/common/data/scans_repo.py` holding the batched scan write, the window count, the ranking publish and the prune; written by checkout, read by analytics
- [X] T016 Bring up the database, run `./scripts/reset_db.sh`, and confirm the seeded state matches the previous weeks: 2,000 items, 10,000 units each, empty scan log

**Checkpoint**: the shared library imports and the database holds the standard starting state.

---

## Phase 3: User Story 1 - A shopper completes a purchase (Priority: P1) 🎯 MVP

**Goal**: The checkout service alone can open a basket, scan into it, and take payment, with stock coming down correctly.

**Independent Test**: Start only the checkout service and call it directly on port 8081. The four transaction paths work and the stock invariant holds. Nothing else needs to be running.

### Tests for User Story 1

- [ ] T017 [P] [US1] Write rule tests in `service-based/tests/test_checkout.py` using stand-in repositories, no database: unknown item is refused, a closed basket is refused, a missing basket is refused, an empty basket cannot be paid for, a paid basket cannot be paid again

### Implementation for User Story 1

- [ ] T018 [US1] Write `service-based/checkout/pyproject.toml`, depending on `common`
- [ ] T019 [US1] Move the checkout rules to `service-based/checkout/app/service.py`: open a basket, add an item, take payment, look one up, and choose between the three refusal messages
- [ ] T020 [P] [US1] Move the four transaction routes to `service-based/checkout/app/api/routes.py` and the error mapping to `service-based/checkout/app/api/error_handlers.py`
- [ ] T021 [US1] Write `service-based/checkout/app/main.py` wiring the repositories and service, and `serve.py` binding port 8081 and carrying the HTTP-version patch from `pipeline/app/serve.py` unchanged
- [ ] T022 [US1] Start the checkout service alone and verify all four paths on port 8081 by hand, including every refusal

**Checkpoint**: a shopper journey works end to end against one service. This is the MVP.

---

## Phase 4: User Story 2 - Separate, independently deployable services (Priority: P1)

**Goal**: Three services and a front door, each startable on its own, none calling another.

**Independent Test**: Start each service alone with the others stopped and confirm it answers the paths it owns. Then start all four and confirm all seven endpoints answer on one address.

### Tests for User Story 2

- [ ] T023 [P] [US2] Write `service-based/tests/test_boundaries.py` asserting that no service imports from another service's package, and that no service holds another service's address; this is the one rule the brief is most explicit about and must be checked mechanically, not by eye

### Implementation for User Story 2

- [ ] T024 [P] [US2] Write `service-based/inventory/pyproject.toml`, depending on `common`
- [ ] T025 [US2] Write `service-based/inventory/app/service.py` serving the product list from memory and the low-stock read, with the threshold falling back to the configured default when the caller omits it
- [ ] T026 [US2] Write `service-based/inventory/app/main.py`, `api/routes.py` and `serve.py` binding port 8082
- [ ] T027 [US2] Start the inventory service alone and verify both paths on port 8082
- [ ] T028 [P] [US2] Write `service-based/analytics/pyproject.toml`, depending on `common`
- [ ] T029 [US2] Write `service-based/analytics/app/service.py` returning the published ranking with its window metadata
- [ ] T030 [US2] Write `service-based/analytics/app/main.py`, `api/routes.py` and `serve.py` binding port 8083
- [ ] T031 [US2] Start the analytics service alone and verify its path on port 8083, expecting an empty ranking rather than an error on a fresh database
- [ ] T032 [P] [US2] Write `service-based/gateway/pyproject.toml`, adding an HTTP client
- [ ] T033 [US2] Write `service-based/gateway/app/routes.py` implementing the routing table in [contracts/README.md](contracts/README.md): longest matching prefix wins; method, path, query, headers and body forwarded unchanged; the owning service's response returned unchanged; an unmatched path answered 404 by the gateway itself
- [ ] T034 [US2] Write `service-based/gateway/app/main.py` and `serve.py` binding port 8080, with no database connection and no business rules, per FR-005a
- [ ] T035 [P] [US2] Write `service-based/scripts/start_all.sh` and `stop_all.sh` starting and stopping the four processes
- [ ] T036 [US2] Start everything and verify all seven endpoints answer through port 8080 with the same shapes the previous weeks produced

**Checkpoint**: the system is genuinely split, and each piece stands alone.

---

## Phase 5: User Story 3 - Scans reach reporting without a service call (Priority: P2)

**Goal**: Items scanned at checkout appear in the ranking, with the two services never speaking.

**Independent Test**: Scan enough items through the gateway to cross the rebuild threshold, wait briefly, then request the ranking and see them, ordered by frequency.

### Tests for User Story 3

- [ ] T037 [P] [US3] Write `service-based/tests/test_scan_flow.py` with stand-in repositories: scans are written in batches rather than one at a time, a full queue drops scans rather than failing a checkout, and a rebuild happens only when due

### Implementation for User Story 3

- [ ] T038 [US3] Move the queue and batched writer to `service-based/checkout/app/ingest.py`, keeping the window and ranker filters out; handing a scan over must not wait on the database, per FR-016
- [ ] T039 [US3] Wire ingest into `service-based/checkout/app/service.py` and start and stop it in `main.py`, draining the queue on shutdown so queued scans are not lost
- [ ] T040 [US3] Add a single-statement "claim a rebuild if the scan log has moved on far enough" method to `service-based/common/common/data/scans_repo.py`, so one poll costs one round trip and two polls cannot both claim the same rebuild
- [ ] T041 [US3] Write `service-based/analytics/app/rebuilder.py`: a background loop polling four to five times a second, counting the window and publishing the ranking when one is due, then discarding scans too old to count again
- [ ] T042 [US3] Start and stop the rebuilder in `service-based/analytics/app/main.py`
- [ ] T043 [US3] Scan more than 500 items through port 8080, then request the ranking and confirm the items appear most-scanned first; this proves the two services coordinated through the database alone

**Checkpoint**: every user story works. The system is feature-complete.

---

## Phase 6: User Story 4 - A grader can reproduce the results (Priority: P3)

**Goal**: Two committed reports, and instructions that produce them from a clean checkout.

**Independent Test**: Follow the written setup from scratch and arrive at a running system and two report files.

- [ ] T044 [US4] Run `uv run pytest` and confirm the whole suite passes
- [ ] T045 [US4] Smoke test all seven endpoints and all six refusal paths through port 8080, comparing against the previous weeks' responses
- [ ] T046 [US4] Stop the other weeks' database containers, reset, restart, and run the load client at 10 stations for 60 seconds into `service-based/reports/`
- [ ] T047 [US4] Reset, restart, and run the load client at 100 stations for 120 seconds into `service-based/reports/`
- [ ] T048 [US4] Run the stock invariant query after each run and confirm zero unexplained discrepancies; also confirm the pipeline reported no dropped scans
- [ ] T049 [US4] Write `service-based/README.md`: the list of services and what each owns, which is the written deliverable, plus setup, reproduction commands, and a four-way results table against the previous weeks
- [ ] T050 [US4] Write `service-based/QUALITY-ATTRIBUTES.md` analysing what the split bought and cost, with any latency regression stated and any explanation labelled as a hypothesis unless measured

**Checkpoint**: the submission is complete.

---

## Phase 7: Polish & Cross-Cutting Concerns

- [ ] T051 [P] Check every new file opens with two or three lines saying what it is for, that comments make sense without another file open, and that no em dash appears anywhere
- [ ] T052 Walk [quickstart.md](quickstart.md) end to end as written, from a clean checkout, and fix anything that does not work
- [ ] T053 Write `service-based/PLAN.md` recording how the week was actually built and what went wrong, following the previous weeks' format
- [ ] T054 Commit and push

---

## Dependencies & Execution Order

### Phase dependencies

- **Setup (Phase 1)**: no dependencies
- **Foundational (Phase 2)**: needs Setup; blocks every user story
- **US1 (Phase 3)**: needs Foundational
- **US2 (Phase 4)**: needs Foundational. The gateway tasks (T033, T034) also need at least one service running to forward to, so in practice they follow T027 or T031
- **US3 (Phase 5)**: needs US1 (the ingest half lives in checkout) and US2 (the rebuild half lives in analytics)
- **US4 (Phase 6)**: needs all three stories, since the load client exercises all of them
- **Polish (Phase 7)**: last

### Story independence

- **US1** stands alone once the shared library exists. It is the MVP.
- **US2** is independent of US1 in principle, but T036 verifies all seven endpoints, which needs checkout from US1.
- **US3** genuinely depends on both, because it is the seam between them.
- **US4** depends on everything, being the measurement.

### Parallel opportunities

- T003, T004, T005 after T001
- T008, T009, T010 together, then T012, T013, T014, T015 together after T011
- The three services in Phase 4 are independent of each other: T024-T027, T028-T031 and T032-T035 are three parallel tracks
- All test tasks (T017, T023, T037) are independent of each other

---

## Parallel Example: Phase 2 Foundational

```bash
# After T007, these four touch different files:
Task: "Move settings to common/common/config.py"
Task: "Move domain errors to common/common/errors.py"
Task: "Move the wire contract to common/common/schemas.py"

# After T011, these four are independent:
Task: "Move the catalogue read to common/common/data/catalog_repo.py"
Task: "Move basket SQL to common/common/data/baskets_repo.py"
Task: "Create common/common/data/stock_repo.py"
Task: "Create common/common/data/scans_repo.py"
```

---

## Implementation Strategy

### MVP first (User Story 1 only)

1. Phase 1: Setup
2. Phase 2: Foundational
3. Phase 3: US1
4. **Stop and validate**: a shopper journey works against the checkout service alone

At that point the system is not yet service-based, but it is correct, and everything after is structural.

### Incremental delivery

1. Setup and Foundational, the shared library exists
2. US1, checkout works alone
3. US2, three services and a front door, all seven endpoints answer on one address
4. US3, scans reach the ranking through the database
5. US4, measure it and write it up

### What to watch

**T033, the gateway, is the riskiest task.** Every request passes through it, and it is expected to be
the slowest part of the system. The spec has already accepted that: no latency figure fails this week.
If it makes runs fail rather than merely slow, [research.md](research.md) decision 2 records the
fallback.

**T040 and T041 are where correctness could quietly slip.** If the claim is not a single statement,
two polls could both rebuild the same window, and the ranking would be wrong in a way no test here
would catch.

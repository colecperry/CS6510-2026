---

description: "Task list for the microservices self-checkout implementation"
---

# Tasks: Microservices Self-Checkout

**Input**: Design documents from `/specs/002-microservices/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/, quickstart.md

**Tests**: Included. The spec asks for them directly: SC-006 requires the no-shared-store rule to be verifiable by inspection, SC-008 requires idempotency "demonstrated by a test rather than argued", and the constitution's weekly workflow requires tests to pass before a week is declared done.

**Organization**: Grouped by user story, so each can be implemented and checked on its own.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to
- Exact file paths are given in every task

## Path Conventions

All paths are relative to the repository root. This week's code lives in `microservices/`, a new top-level folder beside `monolith/`, `layered/`, `pipeline/` and `service-based/`.

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Get the folder, the workspace, the three databases and the reset path in place before any service code exists.

- [X] T001 Create `microservices/` as a copy of `service-based/`, then verify the copy is byte-identical with `diff -r` before changing anything, as the constitution's Weekly Workflow requires
- [X] T002 Write the workspace root `microservices/pyproject.toml` declaring members `["common", "gateway", "checkout", "inventory", "analytics"]`, with `package = false` and the dev group pinning `pytest>=8.3.0` and `pytest-asyncio>=0.24.0`
- [X] T003 Write `microservices/docker-compose.yml` with three Postgres 16 services named `self-checkout-db-checkout`, `self-checkout-db-inventory` and `self-checkout-db-analytics`, on host ports 5437, 5438 and 5439 respectively
- [X] T004 Write `microservices/.env.example` and copy it to `microservices/.env`, holding three separate database URLs plus the inventory and analytics base URLs checkout needs, and the stock-call retry budget
- [X] T005 [P] Write `microservices/db/checkout/schema.sql` with `catalog`, `transactions` (including the new nullable `paying_since TIMESTAMPTZ`), `transaction_items`, `payment_saga` and the `tx_seq` sequence
- [X] T006 [P] Write `microservices/db/inventory/schema.sql` with `catalog`, `stock` (keeping `CHECK (qty >= 0)` and the index on `qty`) and `applied_requests`, whose primary key is `idempotency_key TEXT`
- [X] T007 [P] Write `microservices/db/analytics/schema.sql` with `catalog`, `scan_log`, `popularity_state` (seeded with its single row, `window_size` 1000, `slide_interval` 500), `popularity_snapshot` and the `scan_seq` sequence
- [X] T008 Rewrite `microservices/db/seed.py` to seed all three stores, inserting the identical 2,000 catalogue rows into each and 10,000 units of stock per SKU into inventory only
- [X] T009 Rewrite `microservices/scripts/reset_db.sh` to apply all three schemas and run the seed, then assert the three catalogue row counts match before exiting non-zero on mismatch
- [X] T010 Run `uv sync --all-packages` from `microservices/` and confirm all five members resolve (a workspace syncs only the root without the flag)

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Build the shared library. This phase is where week 5's biggest structural change lands: `common/` keeps the wire contract and loses every repository, so that no service can express a query against another service's tables.

**⚠️ CRITICAL**: No service work can begin until this phase is complete.

- [X] T011 Write `microservices/common/pyproject.toml` pinning `asyncpg==0.31.0`, `fastapi==0.142.1`, `pydantic==2.13.5`, `pydantic-settings==2.15.0` and `python-dotenv==1.2.3` exactly, each with a comment saying the pin exists to stop drift from week 4, per Principle II
- [X] T012 Write `microservices/common/common/config.py` reading three separate database URLs, the inventory and analytics base URLs, and the retry budget, from `.env`
- [X] T013 [P] Write `microservices/common/common/errors.py` with the domain errors, carrying no HTTP status codes
- [X] T014 [P] Write `microservices/common/common/schemas.py` with the request and response shapes, unchanged from week 5 so the wire contract cannot drift
- [X] T015 [P] Write `microservices/common/common/error_handlers.py` mapping each domain error to its status code, as the only file in the project that names one
- [X] T016 Write `microservices/common/common/pool.py` exposing how to open a connection pool given a URL and a maximum size, and holding no SQL
- [X] T017 Confirm `common` imports cleanly, then grep `microservices/common/` for the seven table names and for `SELECT`, `INSERT` and `UPDATE`, expecting no matches, since a shared repository here would silently re-couple the services

**Checkpoint**: The shared contract exists and holds no data access. Service work can begin.

---

## Phase 3: User Story 2 - Each service owns its own data (Priority: P1)

**Goal**: Three services and a gateway, each with its own store, each startable alone.

**Independent Test**: Start one service with the others stopped and confirm it answers the paths it owns. Run the boundary test and confirm no service holds a query against a table it does not own.

**Why this story comes first despite three stories sharing P1**: the shopper journey and the saga both need these processes to exist. This phase builds them; the next two make them do the interesting work.

### Inventory service

- [X] T018 Write `microservices/inventory/pyproject.toml` depending on `common`, with `fastapi==0.142.1`, `hypercorn==0.18.0` and `starlette==1.6.0` pinned exactly
- [X] T019 [P] [US2] Write `microservices/inventory/app/data/catalog_repo.py` holding the catalogue reads, moved unchanged from week 5's `common/common/data/catalog_repo.py`
- [X] T020 [P] [US2] Write `microservices/inventory/app/data/stock_repo.py` holding the low-stock query and the stock decrement, moved from week 5's shared `stock_repo.py`
- [X] T021 [US2] Write `microservices/inventory/app/service.py` with `list_items` and `low_stock`, taking its repositories as constructor arguments so tests can pass stand-ins
- [X] T022 [US2] Write `microservices/inventory/app/api/routes.py` serving `GET /items` and `GET /inventory/low-stock`, with the threshold overridable per request per FR-020
- [X] T023 [US2] Write `microservices/inventory/app/main.py`, `serve.py` and `wiring.py`, binding port 8082, with `serve.py` refusing the HTTP version upgrade exactly as previous weeks do
- [X] T024 [US2] Start inventory alone with everything else stopped and verify both endpoints answer on 8082

### Analytics service

- [X] T025 Write `microservices/analytics/pyproject.toml` depending on `common`, with the same pinned versions as inventory
- [X] T026 [P] [US2] Write `microservices/analytics/app/data/scans_repo.py` including `claim_rebuild_if_due()` as a single `UPDATE ... WHERE ... RETURNING` statement, keeping the guard inside the write as Principle III requires
- [X] T027 [P] [US2] Write `microservices/analytics/app/data/ranking_repo.py` holding the snapshot read and the wholesale snapshot replace
- [X] T028 [US2] Write `microservices/analytics/app/service.py` with `popular_items`, returning an empty ranking rather than an error when nothing has been scanned, per US4 acceptance scenario 1
- [X] T029 [US2] Write `microservices/analytics/app/api/routes.py` serving `GET /analytics/popular-items`
- [X] T030 [US2] Write `microservices/analytics/app/rebuilder.py` with the 250ms poll loop, keeping `poll_once()` separate from the loop so it can be tested without timing
- [X] T031 [US2] Write `microservices/analytics/app/main.py`, `serve.py` and `wiring.py`, binding port 8083 and starting the rebuilder in the lifespan
- [X] T032 [US2] Start analytics alone, insert rows directly into its `scan_log`, and confirm a ranking is published

### Checkout service (structure only; payment lands in Phase 4)

- [X] T033 Write `microservices/checkout/pyproject.toml` depending on `common`, with the pinned versions plus `httpx>=0.28.0`, which checkout now needs because it must call inventory
- [X] T034 [P] [US2] Write `microservices/checkout/app/data/catalog_repo.py` reading checkout's own catalogue replica, so a scan needs no network call
- [X] T035 [US2] Write `microservices/checkout/app/data/baskets_repo.py`, with the scan guard as a single statement conditioned on `status = 'OPEN' AND paying_since IS NULL` so no item can land in a basket whose receipt lines are already fixed
- [X] T036 [US2] Write `microservices/checkout/app/data/saga_repo.py` with the claim as one `UPDATE transactions SET paying_since = now() WHERE transaction_id = $1 AND status = 'OPEN' AND paying_since IS NULL RETURNING ...`, plus inserting and advancing the `payment_saga` row through exactly four states, `PENDING`, `APPLIED`, `DONE` and `COMPENSATED`, where `DONE` and `COMPENSATED` are the only end states
- [X] T037 [US2] Write `microservices/checkout/app/service.py` with `start`, `scan` and `get`, leaving `complete` raising a clear not-yet-implemented error so the gap is obvious rather than silent
- [X] T038 [US2] Write `microservices/checkout/app/api/routes.py` serving the four `/transactions` paths
- [X] T039 [US2] Write `microservices/checkout/app/main.py`, `serve.py` and `wiring.py`, binding port 8081
- [X] T040 [US2] Start checkout alone and verify start, scan and get answer on 8081, and that `status` is never anything but `OPEN`, `COMPLETED` or `CANCELLED` on the wire

### Gateway and the boundary check

- [X] T041 Write `microservices/gateway/pyproject.toml` with `httpx>=0.28.0` and the pinned web stack, and deliberately no dependency on `common` or `asyncpg`
- [X] T042 [US2] Write `microservices/gateway/app/routes.py` with longest-prefix matching over `/transactions`, `/items`, `/inventory` and `/analytics`, reading service addresses from the environment, and routing nothing under `/internal`
- [X] T043 [US2] Write `microservices/gateway/app/main.py` and `serve.py`, binding port 8080
- [X] T044 Write `microservices/scripts/start_all.sh` and `microservices/scripts/stop_all.sh`, recording pids so each service can still be stopped individually
- [X] T045 [US2] Write `microservices/tests/test_boundaries.py` asserting, by parsing the source, that no service references another service's tables, that no service reads another's database URL, and that the gateway imports neither `common` nor `asyncpg`
- [X] T046 [US2] Prove `test_boundaries.py` is not vacuous by planting a violation, watching it fail, then removing it

**Checkpoint**: All four processes run, each owning one store. US2 is demonstrable.

---

## Phase 4: User Story 1 - A shopper completes a purchase (Priority: P1)

**Goal**: The full journey works end to end through the gateway, including a payment that crosses two databases on its happy path.

**Independent Test**: Run the load client against port 8080 with every service healthy. Transactions complete with no errors and stock falls by what was bought.

- [X] T047 [US1] Write `microservices/inventory/app/data/applied_requests_repo.py` recording idempotency keys already applied, treating each key as an opaque string so inventory stays unaware of checkout's orchestration
- [X] T048 [US1] Write `microservices/inventory/app/api/internal_routes.py` serving `POST /internal/stock/reduce`, applying the `idempotencyKey` insert and the stock decrement in one local transaction and returning `alreadyApplied: true` on a duplicate rather than decrementing twice
- [X] T049 [US1] Write `microservices/checkout/app/clients/inventory_client.py` wrapping the outbound call, with its own timeout, so the saga code never touches HTTP details directly
- [X] T050 [US1] Write `microservices/checkout/app/saga/coordinator.py` happy path: claim the basket, read its lines, call inventory to reduce stock, then mark the basket `COMPLETED` and the saga `DONE`. Stock goes first, per research decision D2
- [X] T051 [US1] Wire `complete` in `microservices/checkout/app/service.py` to the coordinator, returning the itemised receipt with repeated scans of a SKU collapsed into one line carrying a quantity, per FR-017
- [X] T052 [P] [US1] Write unit tests in `microservices/checkout/tests/test_saga_happy_path.py` using a stand-in inventory client, covering a successful payment, an empty basket refusal, and a second payment for an already-paid basket
- [X] T053 [US1] Smoke test all seven endpoints and all six error paths through port 8080
- [X] T054 [US1] Capture a receipt and a scan response from port 8080, capture the same two from `service-based/` running on its own ports, and diff their JSON key structure to confirm Principle I holds

**Checkpoint**: The shopper journey works when nothing fails. US1 is demonstrable.

---

## Phase 5: User Story 3 - A purchase that spans two stores stays correct (Priority: P1)

**Goal**: The saga survives failure. This is the phase the assignment is actually about.

**Independent Test**: Force the stock step to fail and confirm the system never leaves a basket claiming payment with stock untouched. Kill checkout mid-saga and confirm recovery drives it to an end state.

- [X] T055 [US3] Add `POST /internal/stock/restore` to `microservices/inventory/app/api/internal_routes.py`, putting back exactly what the named saga took, and succeeding without change when the saga was never applied or was already restored
- [X] T056 [US3] Add `GET /internal/stock/applied/{idempotencyKey}` to `microservices/inventory/app/api/internal_routes.py`, answering whether that key was ever applied, for startup recovery
- [X] T057 [US3] Add the compensation path to `microservices/checkout/app/saga/coordinator.py`: on failure, restore stock if it was applied, clear `paying_since` so the basket is scannable again, mark the saga `COMPENSATED`, and refuse the payment
- [X] T058 [US3] Add the bounded retry to `microservices/checkout/app/clients/inventory_client.py`, with the total attempt budget read from config and held well under the load client's ten second per-request timeout, per FR-013a
- [X] T059 [US3] Write `microservices/checkout/app/saga/recovery.py` exposing a single `recover_once()` that finds `payment_saga` rows left in `PENDING` or `APPLIED`, asks inventory whether each key was applied, and drives each to `DONE` or `COMPENSATED`. No timer and no background task: it runs when it is called
- [X] T060 [US3] Call `recover_once()` from `microservices/checkout/app/main.py`'s lifespan at startup, before the service begins accepting traffic, so a crash is repaired on restart, and make it callable by hand for the demo
- [X] T061 [P] [US3] Write `microservices/checkout/tests/test_saga_compensation.py` asserting that when the stock step fails, the basket returns to open and scannable, the saga reaches `COMPENSATED`, and the payment is refused
- [X] T062 [P] [US3] Write `microservices/inventory/tests/test_idempotency.py` asserting that applying the same `idempotencyKey` twice reduces stock once and reports `alreadyApplied: true` the second time, satisfying SC-008 by test rather than by argument
- [X] T063 [P] [US3] Write `microservices/checkout/tests/test_recovery.py` asserting that a saga left in `PENDING` is driven to `DONE` when inventory reports the key applied, and to `COMPENSATED` when it reports it was not, and that recovery is itself safe to run twice
- [X] T064 [US3] Manually kill checkout between the stock reduction and the basket being marked paid, restart it, and confirm the invariant holds afterwards
- [X] T065 [US3] Stop inventory, attempt a payment, and confirm it is refused within the retry budget rather than hanging until the client times out
- [X] T066 [US3] Drive two concurrent payments for the last unit of one SKU and confirm stock does not go negative and neither reduction is lost

**Checkpoint**: The saga is correct under failure. US3 is demonstrable.

---

## Phase 6: User Story 4 - Scans reach reporting across a boundary (Priority: P2)

**Goal**: Scans recorded by checkout appear in analytics' ranking, without a shopper ever waiting for it.

**Independent Test**: Scan past the 500-item rebuild interval and confirm the ranking appears. Stop analytics mid-run and confirm scan latency is unaffected.

- [X] T067 [US4] Write `microservices/analytics/app/api/internal_routes.py` serving `POST /internal/scans`, accepting a batch of SKUs and returning `202` with the accepted count
- [X] T068 [US4] Write `microservices/checkout/app/clients/analytics_client.py` posting batches, with a short timeout and no retry, because a lost scan costs the ranking accuracy while a blocked writer costs a checkout
- [X] T069 [US4] Write `microservices/checkout/app/ingest.py` with the bounded queue capped at 50,000 and batches capped at 200, posting to analytics instead of writing to a database, and counting drops rather than blocking when full
- [X] T070 [US4] Call the ingest writer from the scan path in `microservices/checkout/app/service.py` without awaiting a network call, so FR-014 holds
- [X] T071 [P] [US4] Write `microservices/checkout/tests/test_ingest.py` asserting scans are sent in batches rather than one at a time, and that a full queue drops and counts rather than failing a customer's checkout
- [X] T072 [US4] Scan more than 500 items through port 8080 and confirm the ranking appears with SKU-000001 first and a Zipf-shaped tail, matching previous weeks
- [X] T073 [US4] Stop analytics mid-run, confirm scan latency is unaffected and no checkout fails, then restart it and confirm the ranking catches up

**Checkpoint**: All four functional stories work. The system is ready to measure.

---

## Phase 7: User Story 5 - The results reproduce (Priority: P3)

**Goal**: Two committed reports, a correctness check that spans three databases, and the measurements the write-up will quote.

**Independent Test**: Follow the written instructions from a clean checkout and arrive at a running system and two reports.

- [X] T074 [US5] Write `microservices/scripts/check_invariant.sh` reading units sold from the checkout store and final quantities from the inventory store and comparing them outside the database, reporting the same four counts as previous weeks
- [X] T075 [US5] Stop the other weeks' containers, reset, start all four, and run the default load: 10 stations for 60 seconds, reporting into `microservices/reports/`
- [X] T076 [US5] Reset and restart, then run the stress load: 100 stations for 120 seconds, reporting into `microservices/reports/`
- [X] T077 [US5] Run `check_invariant.sh` after each run and confirm `unexplained_violations` is zero
- [X] T078 [US5] Query `payment_saga` in the checkout store for rows ending in `COMPENSATED` after each run and record the count, including zero, as FR-027 requires, and confirm no row is left in `PENDING` or `APPLIED`
- [X] T079 [US5] Repeat week 5's per-process CPU measurement during the default run, sampling cumulative CPU time for the four service processes, to test the written prediction that the gateway and not checkout is still the cap

---

## Phase 8: Polish and Cross-Cutting Concerns

- [X] T080 Write `microservices/README.md` with the microservice list and what each owns, which is the written deliverable, plus setup, exact reproduction commands and a five-way results table
- [X] T081 Write `microservices/QUALITY-ATTRIBUTES.md` analysing what database-per-service bought and cost, including what choreography would have changed, and stating explicitly what was verified against what was assumed
- [X] T082 Sweep every `.py` file under `microservices/` for the comment rules: a two or three line header, no comment running past two lines, no hard-wrapped sentences
- [X] T083 Run `uv run pytest` from `microservices/` and from each of the four service directories, and record in the README that there is no linter configured for this project
- [X] T084 Grep every file under `microservices/` for an em dash, expecting none, and confirm every Markdown table's pipes line up vertically
- [X] T085 Walk `specs/002-microservices/quickstart.md` end to end from a clean state to confirm the written instructions actually work

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies
- **Foundational (Phase 2)**: Depends on Setup. Blocks every story
- **US2 (Phase 3)**: Depends on Foundational
- **US1 (Phase 4)**: Depends on US2, because payment needs checkout and inventory to exist as processes
- **US3 (Phase 5)**: Depends on US1, because compensation needs a happy path to compensate
- **US4 (Phase 6)**: Depends on US2 only. Can run in parallel with Phases 4 and 5
- **US5 (Phase 7)**: Depends on US1, US3 and US4, since the reports measure the finished system
- **Polish (Phase 8)**: Depends on US5, since the write-up quotes its numbers

### Why the P1 stories are ordered this way

US1, US2 and US3 all carry P1, so priority alone does not order them. The build order is driven by dependency: US2 creates the processes, US1 makes payment work when nothing fails, US3 makes it survive when something does. Splitting the saga's happy path from its failure paths keeps each phase independently checkable, which a single combined phase would not be.

### Parallel Opportunities

- T005, T006 and T007, the three schemas, are independent files
- T013, T014 and T015 within the shared library
- The three services in Phase 3 are independent of each other: T018-T024, T025-T032 and T033-T040 can proceed in parallel once Phase 2 is done
- All test-writing tasks marked [P]: T052, T061, T062, T063, T071
- Phase 6 (US4) can run alongside Phases 4 and 5

---

## Parallel Example: Phase 3

```bash
# Once the shared library exists, the three services can be built independently:
Task: "Inventory service, T018 through T024"
Task: "Analytics service, T025 through T032"
Task: "Checkout service structure, T033 through T040"
```

---

## Implementation Strategy

### MVP

Phases 1, 2, 3 and 4. That gives four running processes, each owning its own store, and a shopper journey that completes end to end. It is demonstrable and measurable.

It is not yet submittable: without Phase 5 the saga cannot survive a failure, and the assignment's central requirement is coordination across microservices, which means handling the case where coordination fails.

### Incremental delivery

1. Setup and Foundational, then the three stores exist and the contract is shared
2. US2, then each service runs alone and the boundary test passes
3. US1, then a shopper can buy something
4. US3, then the books stay correct when a step fails
5. US4, then reporting works across the boundary
6. US5, then the numbers exist
7. Polish, then the write-up quotes them

---

## Notes

- [P] marks tasks in different files with no dependency on incomplete work
- Commit at the end of each numbered phase, not after individual tasks
- The invariant must be checked after every load run, not only the submitted ones
- Phase 5 is the week's real content. If time runs short, cut Phase 6 before cutting any of it

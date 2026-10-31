---

description: "Task list for the event-driven self-checkout implementation"
---

# Tasks: Event-Driven Self-Checkout

**Input**: Design documents from `/specs/003-event-driven-services/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/, quickstart.md

**Tests**: Included. US2 requires the no-direct-calls rule to be verifiable by inspection, US4's whole value is four failure scenarios that can only be shown by running them, and the constitution's Weekly Workflow requires tests to pass before a week is declared done.

**Organization**: Grouped by user story, so each can be implemented and checked on its own.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to
- Exact file paths are given in every task

## Path Conventions

All paths are relative to the repository root. This week's code lives in `event-driven/`, a new top-level folder beside `monolith/`, `layered/`, `pipeline/`, `service-based/` and `microservices/`.

## Story order, and why it is not the spec's order

US2 is implemented before US1 although both are P1. US1's third acceptance scenario requires stock to fall when a shopper pays, and that now happens through the broker, so the broker has to exist first.

US5 is P1 but comes last, because evidence can only be gathered from a finished system.

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Get the folder, the workspace, three databases, a broker and the reset path in place before any service code changes.

- [X] T001 Create `event-driven/` as a copy of `microservices/`, then verify the copy is byte-identical with `diff -r` before changing anything, as the constitution's Weekly Workflow requires
- [X] T002 Update the workspace root `event-driven/pyproject.toml` so its description names week 7 and its members list stays `["common", "gateway", "checkout", "inventory", "analytics"]`, with `package = false`
- [X] T003 Rewrite `event-driven/docker-compose.yml` with three Postgres 16 services on host ports 5440, 5441 and 5442, container names `self-checkout-db-checkout-ed`, `self-checkout-db-inventory-ed` and `self-checkout-db-analytics-ed`, keeping week 6's `max_connections` of 200, 50 and 50
- [X] T004 Add a `rabbitmq` service to `event-driven/docker-compose.yml` using `rabbitmq:4-management`, container name `self-checkout-broker`, host ports 5672 for AMQP and 15672 for the management UI, with a named volume and a healthcheck running `rabbitmq-diagnostics -q ping`
- [X] T005 Write `event-driven/.env.example` and copy it to `event-driven/.env`, holding the three database URLs on the new ports, `BROKER_URL=amqp://guest:guest@localhost:5672/`, `OUTBOX_POLL_MS`, `SCAN_BATCH_MAX=200`, `SCAN_QUEUE_MAX=50000`, and the three service URLs the Python gateway still needs
- [X] T006 [P] Edit `event-driven/db/checkout/schema.sql`: drop the `paying_since TIMESTAMPTZ` column from `transactions`, drop the `payment_saga` table entirely, and keep `status TEXT NOT NULL CHECK (status IN ('OPEN','COMPLETED','CANCELLED'))` exactly as the contract fixes it
- [X] T007 [P] Add the `outbox` table to `event-driven/db/checkout/schema.sql` with columns `message_id UUID PRIMARY KEY`, `routing_key TEXT NOT NULL`, `payload JSONB NOT NULL`, `created_at TIMESTAMPTZ NOT NULL DEFAULT now()`, `sent_at TIMESTAMPTZ` nullable, plus a partial index `ON outbox (created_at) WHERE sent_at IS NULL`
- [X] T008 [P] Edit `event-driven/db/inventory/schema.sql`: rekey `applied_requests` to `message_id UUID PRIMARY KEY`, keep `lines JSONB NOT NULL` and `applied_at TIMESTAMPTZ NOT NULL DEFAULT now()`, and drop the `restored_at` column, which existed only for compensation
- [X] T009 [P] Verify `event-driven/db/analytics/schema.sql` is unchanged from week 6, including `popularity_state` seeded with `window_size` 1000 and `slide_interval` 500
- [X] T010 Update `event-driven/db/seed.py` only where the connection targets changed, keeping the identical 2,000 catalogue rows in all three stores and 10,000 units per SKU in inventory alone
- [X] T011 Update `event-driven/scripts/reset_db.sh` to apply all three schemas, run the seed, purge both broker queues so a stale message cannot leak into a fresh run, and assert the three catalogue row counts match before exiting non-zero on mismatch
- [X] T012 Pin `aio-pika` in `event-driven/common/pyproject.toml` at an exact version with a comment saying why it is exact, alongside the existing exact pins for `asyncpg`, `fastapi`, `pydantic` and `pydantic-settings`
- [X] T013 Run `uv sync` at `event-driven/` and confirm no previously pinned version moved, by diffing the resolved versions against `microservices/uv.lock`

**Checkpoint**: `docker compose up -d && ./scripts/reset_db.sh` succeeds, the management UI answers on 15672, and nothing has been implemented yet.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: The shared broker mechanics, and the removal of everything the old synchronous design needed.

**CRITICAL**: No user story work can begin until this phase is complete.

- [X] T014 Write `event-driven/common/common/bus.py` holding the broker connection, a `publish` that takes an exchange, a routing key and a body, and a `consume` that registers a handler against a queue. Annotate every parameter and return value, including the handler as `Callable[[dict], Awaitable[None]]`. It MUST hold no message definitions, for the same reason `common/` holds no SQL
- [X] T015 Add broker settings to `event-driven/common/common/config.py`: `broker_url`, `outbox_poll_ms`, `scan_batch_max`, `scan_queue_max`, each typed
- [X] T016 Delete `event-driven/checkout/app/saga/coordinator.py`, `event-driven/checkout/app/saga/recovery.py`, `event-driven/checkout/app/saga/__init__.py` and `event-driven/checkout/app/data/saga_repo.py`. A durable message the broker will deliver replaces what they repaired
- [X] T017 Delete `event-driven/checkout/app/clients/inventory_client.py` and its `InventoryUnavailable` error. Nothing calls inventory any more
- [X] T018 Delete `event-driven/inventory/app/api/internal_routes.py` and the `/internal/stock/*` routes it declares, and remove `restore` and `was_applied` from `event-driven/inventory/app/stock_mover.py`
- [X] T019 Delete `event-driven/checkout/tests/test_saga_happy_path.py`, `test_saga_compensation.py` and `test_recovery.py`, which test code that no longer exists
- [X] T020 Update `event-driven/checkout/app/wiring.py`, `main.py` and `inventory/app/wiring.py` to stop constructing the deleted objects, and confirm each service still starts alone

**Checkpoint**: all three services start, the contract's read endpoints answer, and paying is broken because nothing reduces stock yet. That is expected and the next phase fixes it.

---

## Phase 3: User Story 2 - Services talk only through the broker (Priority: P1)

**Goal**: Every cross-service exchange travels as a message. No service holds another's address.

**Independent Test**: Inspect every service's configuration and find no service address. Watch the management UI while traffic runs and see messages on both queues.

- [X] T021 [US2] Write `event-driven/common/common/bus.py`'s topology declaration: one durable topic exchange named `selfcheckout`, declared idempotently by every service at startup so no service depends on another having started first
- [X] T022 [P] [US2] Write `event-driven/inventory/app/bus/__init__.py` and `consumer.py` declaring the durable quorum queue `inventory.sales` bound to `selfcheckout` with routing key `sale.completed`
- [X] T023 [P] [US2] Write `event-driven/analytics/app/bus/__init__.py` and `consumer.py` declaring the durable quorum queue `analytics.scans` bound to `selfcheckout` with routing key `items.scanned`
- [X] T024 [US2] Write `event-driven/checkout/app/bus/publisher.py` with two publish paths: one persistent with publisher confirms for `sale.completed`, one transient without confirms for `items.scanned`, per research.md D2
- [X] T025 [US2] Set manual acknowledgement in `event-driven/inventory/app/bus/consumer.py` and `event-driven/analytics/app/bus/consumer.py`, acknowledging only after the handler's database transaction commits, so a crash mid-handling causes redelivery rather than loss (FR-007)
- [X] T026 [US2] Set a prefetch count in `event-driven/inventory/app/bus/consumer.py` and `event-driven/analytics/app/bus/consumer.py`, and record the chosen value with its reasoning in a comment, since an unbounded prefetch would let one consumer hold the whole queue
- [X] T027 [US2] Extend `event-driven/tests/test_boundaries.py` with two new checks: no service's settings contain another service's host or port, and no service imports another service's package
- [X] T028 [US2] Verify `event-driven/tests/test_boundaries.py` is not vacuous by planting one violation of each of the five checks in turn, confirming each fails, then reverting
- [X] T029 [US2] Write `event-driven/tests/test_topology.py` asserting the exchange and both queues exist with the expected durability and bindings after a service starts

**Checkpoint**: both queues are visible in the management UI, declared by services that have never heard of each other.

---

> **T032 and T039 were done early, in the chunk that deleted the old design.** Checkout could not start otherwise: removing its service addresses left `AnalyticsClient` with nothing to construct from, and dropping `paying_since` left a scan statement guarding on a column that no longer existed. Both were consequences of the deletions rather than separate work.

## Phase 4: User Story 1 - A shopper completes a purchase (Priority: P1)

**Goal**: The seven endpoints behave exactly as before, with payment now returning before stock is reduced.

**Independent Test**: Run the load client. Zero errors, and the invariant holds once the outbox has drained.

### The outbox and the payment transaction

- [X] T030 [US1] Write `event-driven/checkout/app/data/outbox_repo.py` with `enqueue(conn, message_id, routing_key, payload)` taking a connection so it joins the caller's transaction, `unsent(limit)` reading rows where `sent_at IS NULL` oldest first, and `mark_sent(message_id)`. Annotate every parameter and return value
- [X] T031 [US1] Rewrite the payment path in `event-driven/checkout/app/service.py` as a single transaction that flips the status, reads the receipt lines and enqueues the outbox row, with the guard folded into the statement: `UPDATE transactions SET status = 'COMPLETED', completed_at = now() WHERE transaction_id = $1 AND status = 'OPEN' AND item_count > 0 RETURNING station_id, item_count, running_total, started_at, completed_at`
- [X] T032 [US1] Remove every reference to `paying_since` from `event-driven/checkout/app/data/baskets_repo.py`, and confirm the empty-basket and already-finalised refusals still return `EMPTY_BASKET` and `TRANSACTION_NOT_OPEN` from the status guard alone
- [X] T033 [US1] Write `event-driven/checkout/app/outbox.py` holding the relay: a background loop reading unsent rows, publishing each with publisher confirms, marking it sent only after the broker confirms, and surviving an error rather than dying quietly
- [X] T034 [US1] Give the relay in `event-driven/checkout/app/outbox.py` a backoff when the broker is unreachable, so an outage does not spin the loop, and a comment saying the shopper is never waiting on this
- [X] T035 [US1] Start and stop the relay from `event-driven/checkout/app/main.py`'s lifespan, draining what it can at shutdown

### Inventory applies the reduction

- [X] T036 [US1] Rewrite `event-driven/inventory/app/stock_mover.py` so `reduce` takes a `message_id: UUID` and claims it with `INSERT INTO applied_requests (message_id, lines) VALUES ($1, $2::jsonb) ON CONFLICT (message_id) DO NOTHING RETURNING message_id`, in the same transaction as the stock change
- [X] T037 [US1] Write the `sale.completed` handler in `event-driven/inventory/app/bus/consumer.py`, parsing the contract's fields, calling `reduce`, and acknowledging only after the commit
- [X] T038 [US1] Confirm `apply_delta` in `event-driven/inventory/app/data/stock_repo.py` is unchanged, including `GREATEST(0, qty + $2)`, since the contract still defines no out-of-stock response (FR-020)

### Scans reach analytics

- [X] T039 [US1] Edit `event-driven/checkout/app/ingest.py` so `ScanWriter` publishes an `items.scanned` message instead of posting over HTTP, keeping the queue, the `MAX_BATCH` of 200, the `MAX_PENDING` of 50,000 and the non-blocking `put_nowait`, so the scan path's latency lineage is unchanged
- [X] T040 [US1] Delete `event-driven/checkout/app/clients/analytics_client.py` and the `/internal/scans` route in `event-driven/analytics/app/api/internal_routes.py`
- [X] T041 [US1] Write the `items.scanned` handler in `event-driven/analytics/app/bus/consumer.py`, inserting the batch into `scan_log` in one statement
- [X] T042 [US1] Confirm `event-driven/analytics/app/rebuilder.py` and `data/ranking_repo.py` are unchanged, since how scans arrive changed but how the ranking is built did not

### Tests

- [X] T043 [P] [US1] Update `event-driven/checkout/tests/test_checkout.py` for the new payment path, with no saga and no inventory client in the fakes
- [X] T044 [P] [US1] Write `event-driven/checkout/tests/test_outbox.py` proving the outbox row and the status change commit together, and that a failure to publish leaves the row unsent rather than lost
- [X] T045 [P] [US1] Update `event-driven/inventory/tests/test_idempotency.py` to key on `message_id`, and make every test re-runnable by suffixing ids with `uuid.uuid4().hex` rather than using fixed keys
- [X] T046 [US1] Run the suites under `event-driven/{checkout,inventory,analytics}/tests/` twice consecutively without resetting the database, to catch tests that only pass on a clean store

### End to end

- [X] T047 [US1] Write `event-driven/scripts/smoke_test.sh` exercising all seven endpoints and all six error paths through port 8080, comparing every response shape against `microservices/`
- [X] T048 [US1] Scan past 500 items through `event-driven/scripts/smoke_test.sh` and confirm `GET /analytics/popular-items` returns a ranking, proving the scan path works end to end through the broker

**Checkpoint**: a shopper can buy something, stock comes down shortly afterwards, and the invariant holds once the outbox drains.

---

## Phase 5: User Story 4 - A service can be unavailable without work being lost (Priority: P2)

**Goal**: Prove the property the broker was introduced for. This is the week's real content.

**Independent Test**: Stop a service, drive traffic, restart it, confirm the work is reflected.

- [X] T049 [US4] Write `event-driven/scripts/stop_one.sh` taking a service name and a `--start` flag, so a single service can be stopped and restarted without touching the others
- [X] T050 [US4] Write `event-driven/scripts/measure_lag.sh` which pays for a basket, polls stock until the reduction lands, and prints the delay. FR-011 requires this number stated, not estimated
- [X] T051 [US4] Run the inventory-down scenario with `event-driven/scripts/stop_one.sh inventory`: stop inventory, pay for baskets, confirm every payment returns 200 with a full receipt, restart inventory, confirm the invariant holds exactly. Record the result
- [X] T052 [US4] Run the broker-down scenario with `docker compose stop rabbitmq` from `event-driven/`: stop RabbitMQ, pay for baskets, confirm every payment still returns 200, restart the broker, confirm the relay drains and the invariant holds. A failure here means something publishes inside the request path
- [X] T053 [US4] Run the analytics-down scenario with `event-driven/scripts/stop_one.sh analytics`: stop analytics, scan for 30 seconds, confirm scan latency is unchanged, restart analytics, confirm those scans reach the ranking
- [X] T054 [US4] Measure and record the bound on undelivered messages in `event-driven/README.md`: how many scans the `MAX_PENDING` queue in `event-driven/checkout/app/ingest.py` holds before shedding, and how long the durable queue can accumulate before the broker's own limit applies (FR-019)
- [X] T055 [US4] Write `event-driven/checkout/tests/test_relay_retry.py` proving an unsendable message stays unsent and is retried, rather than being marked sent or dropped
- [X] T056 [US4] Record each scenario's outcome in `event-driven/reports/resilience-scenarios.md` for the write-up, with the commands used, so the figures in QUALITY-ATTRIBUTES are transcribed rather than recalled

**Checkpoint**: three outages survived, one lag figure measured, one shedding bound stated.

---

## Phase 6: User Story 3 - The edge is served by a purpose-built reverse proxy (Priority: P2)

**Goal**: nginx at the edge, with the Python gateway kept so the two can be compared.

**Independent Test**: All seven endpoints behave identically behind each edge, and edge CPU is compared.

- [X] T057 [US3] Move `event-driven/gateway/` to `event-driven/edge/gateway/` and update the workspace members list, so both edges sit side by side
- [X] T058 [US3] Write `event-driven/edge/nginx.conf` routing `/transactions` to 8081, `/items` and `/inventory` to 8082, and `/analytics` to 8083, with longest-prefix matching, listening on 8080
- [X] T059 [US3] Confirm `event-driven/edge/nginx.conf` exposes only the seven public paths and returns 404 for anything else, matching the Python gateway's behaviour exactly (FR-015)
- [X] T060 [US3] Set `proxy_http_version 1.1` and keepalive upstreams in `event-driven/edge/nginx.conf`, with a comment saying the previous weeks' servers refuse the HTTP/2 upgrade and why that patch exists
- [X] T061 [US3] Rewrite `event-driven/scripts/start_all.sh` to take `--edge=nginx` or `--edge=python`, starting exactly one and refusing to start both, since a run with both measures neither
- [X] T062 [US3] Update `event-driven/scripts/stop_all.sh` to stop whichever edge is running, including a natively installed nginx
- [X] T063 [US3] Run `event-driven/scripts/smoke_test.sh` behind each edge in turn and diff the two outputs, confirming every response shape and status code is identical
- [X] T064 [US3] Write `event-driven/scripts/measure_cpu.sh` sampling each process's CPU during a run, so the edge comparison has a number beside the throughput one

**Checkpoint**: one command switches edges, and nothing else about the system moves when it does.

---

## Phase 7: User Story 5 - The submission can be graded (Priority: P1)

**Goal**: Four reports, one diagram, two write-ups, every figure checked.

**Independent Test**: Open the repository and find each artefact present and readable.

- [X] T065 [US5] Write `event-driven/scripts/run_all_benchmarks.sh` performing all four runs with a database reset and a service restart between each, and refusing to proceed if the outbox has not drained
- [X] T066 [US5] Run the graded pair against the nginx edge into `event-driven/reports/`: default parameters, then 100 stations over 120 seconds
- [X] T067 [US5] Run the comparison pair against the Python gateway edge into `event-driven/reports/comparison/`, at the same two parameter sets
- [X] T068 [US5] Check the invariant after every one of the four runs with `event-driven/scripts/check_invariant.sh`, and make that script wait for the outbox to drain rather than reporting a message in flight as a violation
- [X] T069 [US5] Write `event-driven/ARCHITECTURE.md` with the diagram: three services, three stores, the broker with its exchange and two queues, both edges, and which message flows where. This is the graded point
- [X] T070 [US5] Write `event-driven/README.md` covering setup, the nginx dependency and its version, exact reproduction commands for all four runs, and a seven-way results table across every week
- [X] T071 [US5] Write `event-driven/QUALITY-ATTRIBUTES.md` analysing what events bought and cost, reporting the edge effect and the communication effect as separate numbers, and stating what was verified against what was assumed
- [X] T072 [US5] Record the Principle III amendment in `event-driven/QUALITY-ATTRIBUTES.md` with its reasoning, as FR-025 and the constitution's Governance section require
- [X] T073 [US5] Record the self-containment deviation for natively installed nginx in `event-driven/QUALITY-ATTRIBUTES.md`, with why comparability was chosen over it
- [X] T074 [US5] Write the section in `event-driven/QUALITY-ATTRIBUTES.md` on what the saga's disappearance means, naming the four files deleted and why a guaranteed message replaced them. This is the week's most interesting result

**Checkpoint**: everything the grader opens is present.

---

## Phase 8: Polish and Cross-Cutting Concerns

- [X] T075 Write `event-driven/scripts/check_figures.py` comparing every number quoted in `README.md` and `QUALITY-ATTRIBUTES.md` against the four report JSON files, exiting non-zero on a mismatch, and run it
- [X] T076 Run the mechanical checks over every file written or changed under `event-driven/`: no hard-wrapped comments, no em dash, every source file opening with a two or three line header, shebangs intact, markdown tables aligned
- [X] T077 Confirm every file in `event-driven/` opens with a header saying what it is for, in particular the new `bus/` packages and `outbox.py`
- [X] T078 Sweep the comments copied into `event-driven/` for staleness: anything still describing a saga, a compensation, a retry budget or a direct call to inventory is now wrong and must go
- [X] T079 Confirm every parameter and return value in the new code under `event-driven/` is annotated, including the broker handler signatures, per the type annotation rule
- [X] T080 Run every suite under `event-driven/{checkout,inventory,analytics}/tests/` from its own directory, and `event-driven/tests/` from the workspace root, and report pass counts
- [X] T081 State explicitly in `event-driven/QUALITY-ATTRIBUTES.md` what was verified by running it and what was assumed, as Principle IV requires
- [ ] T082 Commit `event-driven/` in full, push, and confirm the submission URL resolves to a repository containing all four reports and the diagram

---

## Dependencies

```
Phase 1 Setup
    |
Phase 2 Foundational        <- deletes the old design, blocks everything
    |
Phase 3 US2 broker plumbing <- US1 depends on this existing
    |
Phase 4 US1 shopper journey
    |
    +---> Phase 5 US4 resilience   (needs a working journey to interrupt)
    |
    +---> Phase 6 US3 edge         (independent of US4; could run in parallel)
                |
Phase 7 US5 evidence        <- needs both edges and a correct system
    |
Phase 8 Polish
```

US4 and US3 do not depend on each other and can be done in either order.

## Parallel execution examples

```bash
# Phase 1, once the folder is copied:
Task: "Three schema files, T006 through T009"

# Phase 3, once the exchange is declared:
Task: "Inventory queue, T022"
Task: "Analytics queue, T023"

# Phase 4, once the implementation lands:
Task: "Three test files, T043 through T045"
```

---

## Implementation Strategy

### MVP

Phases 1 through 4. That gives three services talking only through a broker, and a shopper journey that completes end to end with stock following behind.

It is demonstrable and measurable, but not yet submittable. Without Phase 5 the week has added a broker without showing what it bought, which is the only argument for having added it.

### Incremental delivery

1. Setup and Foundational, then the old design is gone and the broker is running
2. US2, then the services are wired to the broker and cannot reach each other
3. US1, then a shopper can buy something
4. US4, then the resilience claim is proven rather than asserted
5. US3, then the edge question has a number
6. US5, then the evidence exists
7. Polish, then the write-ups quote it correctly

---

## Notes

- [P] marks tasks in different files with no dependency on incomplete work
- Commit at the end of each numbered phase, not after individual tasks
- The invariant must be checked after every load run, not only the four submitted ones, and only after the outbox has drained
- Phase 5 is the week's real content. If time runs short, cut Phase 6 before cutting any of it, and say in the README that the edge comparison was not performed
- T016 through T019 delete code. Verify `diff -r` against `microservices/` was clean first, so what is being deleted is known

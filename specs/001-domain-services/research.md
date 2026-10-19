# Research: Service-Based Self-Checkout

**Date**: 2026-10-13
**Feature**: [spec.md](spec.md)

Seven decisions, each resolving an unknown in the plan's Technical Context.

---

## 1. Starting point

**Decision**: Copy `pipeline/` (the pipes-and-filters week) into a new `service-based/` folder.

**Rationale**: FR-005 forbids one domain service calling another, so the selling service must write scans to the database itself rather than asking the reporting service to.
`pipeline/` already does exactly that: its ingest filter takes scans off an in-memory queue and writes them in batches, without the shopper's request waiting.
That machinery lives entirely inside what becomes the selling service, so it carries over unchanged and FR-016 is satisfied for free.

**Alternatives considered**:

- **`layered/`**: simpler, but its analytics write is synchronous on the request path. Starting there means the selling service would block on a database write per scan, giving back last week's improvement and violating FR-016.
- **`monolith/`**: the layer split would have to be redone before the service split could start.
- **From scratch**: puts the comparability requirement at risk, since incidental differences become alternative explanations for any latency change.

---

## 2. The entry point

**Decision**: A small application of the same kind as the services, forwarding requests by URL path. It holds no business rules and opens no database connection.

**Rationale**: FR-005a requires one address and no business logic.
Writing it in the same stack as everything else keeps the project to one language and one way of running things, which matters more here than raw speed.
It is expected to be the slowest part of the system, and the spec explicitly accepts that.

**Alternatives considered**:

- **An off-the-shelf reverse proxy** (nginx, Caddy): faster, and a stronger argument that the entry point is infrastructure rather than code. Rejected because it adds a second technology and a second way to configure things, for a benefit the spec has already said it does not need. Reconsider only if the entry point turns out to make runs fail rather than merely slow.
- **One service doubling as the entry point**: would make that service forward to the others, which is precisely the service-to-service communication FR-005 forbids.

**Risk accepted**: every request now passes through an extra process. Scans are expected to get slower. Per the clarified SC-001, that is a result to report, not a threshold to pass.

---

## 3. How reporting learns about new scans

**Decision**: The reporting service polls. On a fixed interval it asks the database whether enough scans have accumulated since the last ranking, and rebuilds if so.

**Rationale**: FR-005 rules out the selling service telling it.
Polling is the simplest mechanism that reads only shared state.
A tick costs one statement, and the question "has the scan log moved on far enough?" can be asked and claimed in that same statement, so there is no gap in which two ticks could both decide to rebuild.

The interval is chosen against the existing behaviour: the ranking rebuilds every 500 scans, and under heavy load the system scans roughly 1,300 times a second, so a rebuild is due about every 400ms.
Polling four or five times a second keeps the ranking about as fresh as the previous weeks while costing a handful of trivial queries per second.

**Alternatives considered**:

- **Database notifications** (Postgres `LISTEN`/`NOTIFY`): the selling service signals, the reporting service listens. Still database-mediated, so it would not violate FR-005, and it would remove the polling delay. Rejected as an extra concept to explain and debug for a freshness gain the spec does not ask for.
- **Rebuild lazily, when the ranking is requested**: no background work at all, but it puts a potentially slow rebuild inside a request, and the load client asks for the ranking exactly once, at the end, so it would almost never run.

---

## 4. Where the pipeline's filters end up

**Decision**: The ingest filter stays in the selling service. The window and ranker filters move to the reporting service, driven by the poll rather than by a queue.

**Rationale**: An in-memory queue cannot cross a process boundary, so the pipeline has to split somewhere.
It splits at the database, which is the coupling point the brief names.
Ingest belongs with selling because it is what keeps scans off the request path.
Window and ranker belong with reporting because rebuilding the ranking is reporting's business function.

**Alternatives considered**:

- **All three filters in the reporting service**: would mean the selling service writes each scan synchronously, costing FR-016.
- **All three in the selling service**: would put the ranking rebuild inside the service that handles checkout traffic, which is both the wrong owner and the busiest process.

---

## 5. Sharing the database access code

**Decision**: One shared library containing the connection handling and the query code, installed by each service as a local dependency. Each service imports only the parts it uses.

**Rationale**: The brief asks for this explicitly, and FR-007 requires a single definition of any query used by more than one service.
Two queries genuinely are shared: reading the product catalogue, which all three services need, and the scan log, which selling writes and reporting reads.
Duplicating those would let them drift silently.

**Tradeoff, stated rather than hidden**: a shared library is itself a form of coupling.
Changing a query means every service that uses it must be redeployed.
That is the accepted cost, and it is the characteristic trade of service-based architecture as against microservices, where each service would own its own copy and its own tables.

**Alternatives considered**:

- **Each service keeps its own copy**: maximum independence, the microservices answer. Rejected because it puts the same catalogue query in three files that can diverge, and the brief points the other way.
- **One service owns each table, others ask it**: the cleanest ownership story, but it forces service-to-service calls, which FR-005 forbids.

---

## 6. How the services are built and run

**Decision**: One workspace at the folder root with each service as a member declaring its own dependencies and its own entry point. A script starts all four together for convenience.

**Rationale**: FR-004 requires each service to be independently startable, and each one is: its own process, its own command, its own dependency list.
A single workspace keeps one resolved set of library versions across all of them, which the constitution's comparability principle requires, since drift between services would be drift within the week.

**Alternatives considered**:

- **A separate environment per service**: more faithful to independent deployment, but four separate dependency resolutions can drift apart, and the constitution treats that as a defect.
- **A container per service**: the most convincing demonstration of independent deployability. Rejected as out of scope per the spec's assumptions: the brief says "whatever works", and the requirement is that each service is independently startable, not that it ships as an image.

---

## 7. Database connections across several processes

**Decision**: Size each service's pool to its traffic rather than giving each one the previous week's single-process allowance. The selling service takes nearly all the load and gets most of the connections; the other two get small pools.

**Rationale**: The previous weeks ran one process holding up to 120 connections against a server configured for 200.
Three processes each claiming 120 would exceed the server's limit and fail at startup under load.
Traffic is heavily skewed: the shopper journey is almost all of it, while the two reporting endpoints are called once each per run.

**Alternatives considered**:

- **Equal pools**: wastes connections on two nearly idle services and starves the busy one.
- **Raise the server's connection limit**: avoids thinking about the split, but changes the database configuration, which the constitution requires to stay identical across weeks for comparability.

---

## Resolved unknowns

Every `NEEDS CLARIFICATION` from the Technical Context is now answered.

| Unknown                              | Resolved by                                              |
|--------------------------------------|----------------------------------------------------------|
| Starting point                       | Decision 1                                               |
| Entry point technology               | Decision 2                                               |
| Cross-service coordination mechanism | Decisions 3 and 4                                        |
| Shared data access strategy          | Decision 5                                               |
| Deployment and dependency management | Decision 6                                               |
| Connection pool sizing               | Decision 7                                               |
| Performance targets                  | Not an unknown: the spec deliberately sets none (SC-001) |

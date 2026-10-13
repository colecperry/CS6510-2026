# Feature Specification: Service-Based Self-Checkout

**Feature Branch**: `spec-kit-adoption` (no per-feature branch; the git extension is not installed)

**Created**: 2026-10-13

**Status**: Draft

**Input**: User description: "Add a service-based implementation of the self-checkout system in a new service-based/ folder, alongside the existing monolith/, layered/ and pipeline/ weeks. The system must be split into separate domain services, divided by business function. Each service is its own independently deployable unit, started and stopped on its own. All services share a single centralised database. Services must not communicate with each other directly. Where two services need the same information, they coordinate through the shared database rather than by calling each other. The existing API contract does not change: the same seven endpoints, the same request and response shapes, the same error codes. The load client is unchanged and still points at a single address. The deliverable is the list of services defined, plus two load-client JSON reports from the default and stress parameter sets."

## Clarifications

### Session 2026-10-13

- Q: Does the rule that services must not communicate directly also forbid a thin entry point that receives every request and forwards it to the service that owns it? → A: Allowed. It is the caller-facing tier, not a domain service, and the rule is read as forbidding one domain service from calling another.
- Q: How finely should the system be divided into domain services? → A: Three, one per business function: selling, stock, and reporting.
- Q: Is there a point at which a latency regression forces a design change rather than simply being reported? → A: No. Any slowdown is accepted and reported. The design changes only if the system fails: errors appear, the stock invariant breaks, or a run cannot finish.

## User Scenarios & Testing *(mandatory)*

The users here are the course grader, who reads the submission and runs the load client, and the student maintaining six parallel implementations of one system.

### User Story 1 - A shopper completes a purchase (Priority: P1)

A shopper at a self-checkout station starts a transaction, scans several items and sees a running total, then pays and receives an itemised receipt.
Stock for the items they bought comes down by exactly the quantity they took.

**Why this priority**: This is the product. If it does not work, nothing else about the architecture matters, and the load client cannot produce a report at all.

**Independent Test**: Run the load client against the system at its single address. It completes transactions with a zero error rate, and the stock invariant holds afterwards.

**Acceptance Scenarios**:

1. **Given** a station with no open transaction, **When** the shopper starts one, **Then** an empty basket is created and identified.
2. **Given** an open basket, **When** the shopper scans a known item, **Then** the item count and running total increase, and the item's name and price are returned.
3. **Given** a basket holding items, **When** the shopper pays, **Then** an itemised receipt is returned and stock falls by the quantity bought.
4. **Given** a basket that has already been paid for, **When** the shopper scans another item, **Then** the request is refused and the shopper is told the transaction is no longer open.
5. **Given** an empty basket, **When** the shopper tries to pay, **Then** the request is refused and the shopper is told the basket is empty.

---

### User Story 2 - The system runs as separate, independently deployable services (Priority: P1)

The system is divided along business function, and each part can be started, stopped and reasoned about on its own.
No part calls another part.
Where two parts need the same information, the shared database is how they get it.

**Why this priority**: This is the assignment. It shares P1 with the shopper journey because the submission is worthless if either is missing: a working checkout with one process is not this week's work, and a correct service split that cannot complete a transaction produces no reports.

**Independent Test**: Start one service alone, with the others stopped, and confirm it answers the requests it owns. Inspect the system and confirm no service sends a request to another.

**Acceptance Scenarios**:

1. **Given** the whole system is stopped, **When** a single service is started alone, **Then** it answers the requests belonging to its business function.
2. **Given** all services are running, **When** the system is examined, **Then** no service issues a request to another service.
3. **Given** all services are running, **When** one service is stopped, **Then** the others continue answering their own requests.
4. **Given** the services are running, **When** their data access is examined, **Then** all of them are using one database.

---

### User Story 3 - Scans recorded by one service appear in another's reporting (Priority: P2)

Items scanned at checkout appear in the most-popular-items ranking, even though the part of the system that serves that ranking never hears from the part that handled the scan.

**Why this priority**: This is the hardest consequence of forbidding service-to-service calls, and the one most likely to be got wrong. It is P2 rather than P1 because the checkout journey still works without it, but the submission would be visibly incomplete.

**Independent Test**: Scan items through the system, then ask for the popular-items ranking and confirm the scanned items appear, ranked by frequency.

**Acceptance Scenarios**:

1. **Given** no items have been scanned, **When** the ranking is requested, **Then** an empty ranking is returned rather than an error.
2. **Given** enough items have been scanned to trigger a recalculation, **When** the ranking is requested, **Then** the most frequently scanned items appear in descending order.
3. **Given** items are being scanned continuously, **When** the ranking is requested repeatedly, **Then** it reflects recent scanning activity without the shopper's scan ever having waited for it.

---

### User Story 4 - A grader can reproduce the results (Priority: P3)

Someone with the repository can start the system, run the load client twice, and obtain the submitted numbers.

**Why this priority**: Required for marking and for the comparison against previous weeks, but it depends on the other three working first.

**Independent Test**: Follow the written instructions from a clean checkout and arrive at a running system and two reports.

**Acceptance Scenarios**:

1. **Given** the repository and the documented prerequisites, **When** the setup instructions are followed, **Then** the system starts and answers requests.
2. **Given** a running system, **When** the load client is run with each documented parameter set, **Then** two report files are produced.

---

### Edge Cases

- A shopper scans a barcode that does not exist in the catalogue.
- A shopper scans into, or pays for, a basket that does not exist.
- A shopper pays for the same basket twice.
- A shopper tries to start a transaction without identifying their station.
- An item sells out during a run. The contract defines no out-of-stock response, so stock must stop at zero rather than going negative.
- One service is unavailable while another is running. Requests belonging to the stopped service fail; requests belonging to the running ones must not.
- Two services read and write the same information at the same time, with no direct coordination between them.
- Reporting falls behind the checkout traffic feeding it.

## Requirements *(mandatory)*

### Functional Requirements

**The contract**

- **FR-001**: The system MUST expose the same seven endpoints as the previous implementations, at a single network address, with identical request and response shapes, field names, and error codes.
- **FR-002**: The system MUST work with the existing load client with no modification to that client.

**The service split**

- **FR-003**: The system MUST be divided into three services, each owning one business function:
  - **Selling**: opening a basket, adding items to it, paying for it, and looking one up.
  - **Stock**: what the shop sells, and which items are running low.
  - **Reporting**: which items are being scanned most often.
- **FR-004**: Each service MUST be independently deployable: startable and stoppable on its own, without requiring another service to be running in order to serve its own requests.
- **FR-005**: No domain service MAY send a request to another domain service. Where two services need the same information, they MUST coordinate through the shared database.
- **FR-005a**: A single entry point MUST receive every incoming request and forward it to the service that owns it, so that callers see one address. This entry point is the caller-facing tier, not a domain service: it MUST hold no business rules and MUST NOT read or write the database. Its existence does not count as service-to-service communication.
- **FR-006**: All services MUST use one centralised database.
- **FR-007**: Data access logic needed by more than one service MUST have a single definition, rather than being independently reimplemented per service.
- **FR-008**: The submission MUST list the services defined and what business function each owns.

**Checkout behaviour**

- **FR-009**: A shopper MUST be able to open a basket for a station, add items to it one at a time, and pay for it.
- **FR-010**: Scanning an item MUST return that item's name and price along with the basket's updated count and total.
- **FR-011**: Paying MUST return an itemised receipt, with repeated scans of the same item collapsed into one line carrying a quantity.
- **FR-012**: Paying MUST reduce stock by the quantity bought, and MUST do so as one all-or-nothing change together with marking the basket paid.
- **FR-013**: The system MUST refuse to add items to a basket that is not open, to pay for a basket that is empty, and to pay for a basket twice, telling the caller which of those happened.

**Reporting behaviour**

- **FR-014**: The system MUST report which items have been scanned most often recently, recalculated periodically rather than on every scan.
- **FR-015**: The system MUST report which items are below a stock threshold, with the threshold overridable per request.
- **FR-016**: Recording a scan for reporting purposes MUST NOT delay the response to the shopper who made it.

**Correctness**

- **FR-017**: For every item, the stock sold MUST equal the reduction in stock held. Stock MUST never go below zero.
- **FR-018**: Two shoppers paying at the same time for the same item MUST NOT lose either reduction.

**Evidence**

- **FR-019**: The submission MUST include two load-client report files, from the default and stress parameter sets used by the previous implementations.
- **FR-020**: The submission MUST state what was verified and what was assumed, and MUST report any measured regression against the previous implementations rather than omitting it.

### Key Entities

- **Catalogue item**: something the shop sells. Has an identifying code, a name and a price. Never changes while the system is running.
- **Stock level**: how many units of an item remain. Reduced when a basket is paid for; read when reporting what is running low.
- **Basket**: one shopper's visit to a station. Has a state of open, paid or abandoned, a running count and total, and the times it opened and closed.
- **Basket line**: one scanned unit within a basket. Records the name and price at the time of sale, so a receipt reflects what was charged.
- **Scan record**: one scanned unit, recorded separately from the basket, used only for reporting. Older records are discarded once no future report can include them.
- **Ranking**: the current most-scanned items, with their positions and counts, plus which stretch of scanning activity produced it.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A shopper can scan an item and see the updated basket without a perceptible wait, at both the normal and the heavy load levels used in previous weeks. No latency figure can fail this week on its own: the measured cost of splitting the system is a result to be reported and compared against the previous implementations, not a threshold to pass.
- **SC-002**: Under the normal load level, every request succeeds. Zero errors.
- **SC-003**: Under the heavy load level, the error rate stays at or below the level recorded by the previous implementation, and any errors are explained with evidence rather than assumed benign.
- **SC-004**: After every run, stock sold equals stock reduced for every item, with no unexplained discrepancy and nothing below zero.
- **SC-005**: Each service can be started with every other service stopped, and answers the requests it owns.
- **SC-006**: No service sends a request to another service. Verifiable by inspection.
- **SC-007**: The most-popular-items ranking produced is materially the same as the previous implementations produced under the same load, confirming the split did not change reporting behaviour.
- **SC-008**: Someone following the written setup instructions from a clean checkout reaches a running system and can reproduce both reports.

## Assumptions

- **The entry point is settled, not assumed.** See Clarifications and FR-005a. The load client can only be pointed at one address, so an entry point is required. It is treated as the caller-facing tier rather than a domain service, and the submission will state that reasoning explicitly rather than leave it implicit.
- **Scope.** The three existing implementations are not modified. This is an addition.
- **Comparability.** The load parameters, starting data, and database schema match the previous weeks, so measured differences are attributable to the architecture.
- **Reporting may lag.** Inventory counts must be exact; the popular-items ranking may be slightly behind. The previous implementations already recalculated only periodically, so some staleness is pre-existing and accepted. Any additional lag introduced by the split will be stated.
- **Shared tables are expected.** Because services coordinate through the database rather than by calling each other, more than one service reading or writing the same table is the intended mechanism, not a design fault.
- **Performance may regress, and that is accepted.** See Clarifications. Directing every request through an entry point, and splitting work across processes, is expected to cost latency. The result is a finding to be measured and reported. The design is revisited only if the system fails outright: errors appear, the stock invariant breaks, or a run cannot finish.
- **Deployment target.** Services run as separate local processes on one machine. Containerising each is out of scope; the requirement is that each is independently startable, not that it ships as an image.

# Feature Specification: Microservices Self-Checkout

**Feature Branch**: `002-microservices` (no per-feature branch; the git extension is not installed)

**Created**: 2026-10-19

**Status**: Draft

**Input**: User description: "Now redesign your solution to be based on independently deployable microservices. Each microservice - probably based on the services you identified in the last assignment - should have its own data store. Requirements to update multiple databases for an API request should be coordinated across microservices. You can use an API Gateway and orchestration or choreography. Usual submission requirements."

## Clarifications

### Session 2026-10-19

- Q: How should a payment that changes both the basket store and the stock store be coordinated? → A: Orchestration. Checkout drives the sequence and issues compensating actions when a step fails. Choreography is rejected because the fixed contract demands a synchronous receipt, so an event round trip would have to be blocked on anyway, and because a message broker is a moving part this comparison does not need. What choreography would have changed is to be recorded in the week's analysis rather than built.
- Q: What happens when the inventory service is unavailable at the moment a shopper pays? → A: Retry the stock reduction briefly inside the request, and if it still fails, undo the payment and refuse it. Stock stays exact, and the path should never trigger during a benchmark where every service is up.
- Q: When must the stock invariant hold? → A: At the instant each payment returns. Stock is reduced before the receipt goes back, so inventory is never visibly wrong and Principle III stands unamended.

## User Scenarios & Testing *(mandatory)*

The users here are the course grader, who reads the submission and runs the load client, and the student maintaining six parallel implementations of one system.

### User Story 1 - A shopper completes a purchase (Priority: P1)

A shopper at a self-checkout station starts a transaction, scans several items and sees a running total, then pays and receives an itemised receipt.
Stock for the items they bought comes down by exactly the quantity they took.

**Why this priority**: This is the product.
If it does not work, nothing else about the architecture matters, and the load client cannot produce a report at all.

**Independent Test**: Run the load client against the system at its single address.
It completes transactions with a zero error rate, and the stock invariant holds afterwards.

**Acceptance Scenarios**:

1. **Given** a station with no open transaction, **When** the shopper starts one, **Then** an empty basket is created and identified.
2. **Given** an open basket, **When** the shopper scans a known item, **Then** the item count and running total increase, and the item's name and price are returned.
3. **Given** a basket holding items, **When** the shopper pays, **Then** an itemised receipt is returned and stock falls by the quantity bought.
4. **Given** a basket that has already been paid for, **When** the shopper scans another item, **Then** the request is refused and the shopper is told the transaction is no longer open.
5. **Given** an empty basket, **When** the shopper tries to pay, **Then** the request is refused and the shopper is told the basket is empty.

---

### User Story 2 - Each service owns its own data, and nothing else reaches it (Priority: P1)

The system is divided along business function.
Each part keeps its data in a store that only it can reach.
No part reads or writes another part's store, and stopping one part does not stop the others answering their own requests.

**Why this priority**: This is the assignment.
Last week's services already split the code by business function but still shared one database, so the coupling simply moved.
Giving each service its own store is the change that makes them microservices rather than a distributed monolith.

**Independent Test**: Start one service alone with the others stopped, and confirm it answers the requests it owns.
Inspect every service's data configuration and confirm each points at a different store and no service holds credentials for another's.

**Acceptance Scenarios**:

1. **Given** the whole system is stopped, **When** a single service is started alone, **Then** it answers the requests belonging to its business function.
2. **Given** all services are running, **When** their data configuration is examined, **Then** each one connects to a store that no other service connects to.
3. **Given** all services are running, **When** one service is stopped, **Then** the others continue answering their own requests.
4. **Given** the system is examined, **When** data access is traced, **Then** no service issues a query against a store it does not own.

---

### User Story 3 - A purchase that spans two data stores leaves the books correct (Priority: P1)

Paying for a basket changes two things that now live in different stores: the basket becomes paid, and stock comes down.
There is no longer any single transaction that can cover both.
The system must coordinate the two so that either both happen or neither appears to have happened, and must recover when one of them fails.

**Why this priority**: This is the specific difficulty the assignment is built around, and the one place the architecture can silently lose money.
It shares P1 with the two stories above because a submission that splits the stores but handles a half-completed payment by shrugging has not done the assignment.

**Independent Test**: Force the stock update to fail during a payment and confirm the system does not end up with a basket marked paid and stock untouched, nor the reverse.
Run the full load and confirm every item's stock reduction matches what was sold.

**Acceptance Scenarios**:

1. **Given** a basket with items, **When** the shopper pays and every participant succeeds, **Then** the basket is marked paid, stock is reduced, and a receipt is returned.
2. **Given** a basket with items, **When** the stock reduction fails, **Then** the basket does not remain in a state that claims it was paid for while stock is untouched.
3. **Given** a payment that has partially completed, **When** the system recovers, **Then** it reaches one of the two valid end states rather than staying split.
4. **Given** the same coordination step is delivered twice, **When** it is applied, **Then** stock falls by the purchased quantity once, not twice.
5. **Given** two shoppers pay at the same moment for the last unit of the same item, **When** both are processed, **Then** stock does not go negative and no reduction is lost.

---

### User Story 4 - Scans recorded by one service appear in another's reporting (Priority: P2)

Items scanned at checkout appear in the most-popular-items ranking, even though the ranking is produced by a service that keeps its data somewhere the checkout service cannot read.

**Why this priority**: Last week this was solved by both services reading one table.
That option is gone, so the information has to cross a service boundary deliberately.
It is P2 rather than P1 because the checkout journey still works without it, but the submission would be visibly incomplete.

**Independent Test**: Scan items through the system, then ask for the popular-items ranking and confirm the scanned items appear, ranked by frequency.

**Acceptance Scenarios**:

1. **Given** no items have been scanned, **When** the ranking is requested, **Then** an empty ranking is returned rather than an error.
2. **Given** enough items have been scanned to trigger a recalculation, **When** the ranking is requested, **Then** the most frequently scanned items appear in descending order.
3. **Given** items are being scanned continuously, **When** the ranking is requested repeatedly, **Then** it reflects recent scanning activity without the shopper's scan ever having waited for it.
4. **Given** the reporting service is stopped while scanning continues, **When** it is restarted, **Then** scanning was not interrupted and the ranking catches up.

---

### User Story 5 - A grader can reproduce the results (Priority: P3)

Someone with the repository can start the system, run the load client twice, and obtain the submitted numbers.

**Why this priority**: Required for marking and for the comparison against previous weeks, but it depends on the other stories working first.

**Independent Test**: Follow the written instructions from a clean checkout and arrive at a running system and two reports.

**Acceptance Scenarios**:

1. **Given** the repository and the documented prerequisites, **When** the setup instructions are followed, **Then** every data store is created and seeded and the system answers requests.
2. **Given** a running system, **When** the load client is run with each documented parameter set, **Then** two report files are produced.

---

### Edge Cases

- A shopper scans a barcode that does not exist in the catalogue.
- A shopper scans into, or pays for, a basket that does not exist.
- A shopper pays for the same basket twice.
- An item sells out during a run. The contract defines no out-of-stock response, so stock must stop at zero rather than going negative.
- The service that owns stock is unavailable at the moment a payment needs to reduce it.
- A payment is interrupted after one store has been changed and before the other has.
- A coordination message is delivered twice, or arrives out of order.
- A compensating action, sent to undo a step, itself fails.
- The reporting service falls behind, or is stopped entirely, while scanning continues.
- Two services hold their own copy of the same catalogue information and must not disagree about an item's price on a receipt.

## Requirements *(mandatory)*

### Functional Requirements

**The contract**

- **FR-001**: The system MUST expose the same seven endpoints as the previous implementations, at a single network address, with identical request and response shapes, field names, and error codes.
- **FR-002**: The system MUST work with the existing load client with no modification to that client.

**The service split**

- **FR-003**: The system MUST be divided into three microservices, each owning one business function:
  - **Checkout**: opening a basket, adding items to it, paying for it, and looking one up.
  - **Inventory**: what the shop sells, how many units remain, and which items are running low.
  - **Analytics**: which items are being scanned most often.
- **FR-004**: Each microservice MUST own a separate data store. No service may read or write a store owned by another service.
- **FR-005**: Each microservice MUST be independently deployable: startable and stoppable on its own, and able to serve the requests it owns without requiring another service to be running, except where a request inherently needs data another service owns.
- **FR-006**: A single entry point MUST receive every incoming request and forward it to the service that owns it, so that callers see one address. This entry point holds no business rules and owns no data store.
- **FR-007**: Where a service needs reference information owned by another service in order to answer its own requests, it MUST hold its own copy rather than querying the other service's store directly.
- **FR-008**: The submission MUST list the microservices defined, what business function each owns, and what data each one's store holds.

**Coordinating a change across stores**

- **FR-009**: Paying for a basket changes data in more than one store. The system MUST coordinate those changes so that the outcome is either "basket paid and stock reduced" or "basket not paid and stock not reduced", and never a lasting mixture of the two.
- **FR-010**: The system MUST coordinate these changes by orchestration: one component drives the sequence of steps, records how far it has got, and issues compensating actions to undo applied steps when a later one fails. No participant may be left to infer the outcome from events alone.
- **FR-011**: When a step in a multi-store change fails, the system MUST undo the steps already applied, rather than leaving them in place.
- **FR-012**: Every coordination step MUST be safe to apply more than once, so that a retried or duplicated message does not reduce stock twice or pay for a basket twice.
- **FR-013**: When a service that must take part in a payment is unavailable, the system MUST retry that step inside the request for a bounded period, and if it still does not succeed, MUST undo any steps already applied and refuse the payment. The basket MUST be left in a state the shopper can retry from. The system MUST NOT return a receipt for a payment whose stock reduction has not been applied.
- **FR-013a**: The bounded retry period MUST be short enough that a refused payment is still returned within the load client's per-request timeout, so that a failure is reported as a refusal rather than as a hung request.
- **FR-014**: Recording a scan for reporting purposes crosses a store boundary and MUST NOT delay the response to the shopper who made it.

**Checkout behaviour**

- **FR-015**: A shopper MUST be able to open a basket for a station, add items to it one at a time, and pay for it.
- **FR-016**: Scanning an item MUST return that item's name and price along with the basket's updated count and total.
- **FR-017**: Paying MUST return an itemised receipt, with repeated scans of the same item collapsed into one line carrying a quantity.
- **FR-018**: The system MUST refuse to add items to a basket that is not open, to pay for a basket that is empty, and to pay for a basket twice, telling the caller which of those happened.

**Reporting behaviour**

- **FR-019**: The system MUST report which items have been scanned most often recently, recalculated periodically rather than on every scan.
- **FR-020**: The system MUST report which items are below a stock threshold, with the threshold overridable per request.
- **FR-021**: The ranking MUST remain materially the same as previous weeks produced under the same load.

**Correctness**

- **FR-022**: For every item, the stock sold MUST equal the reduction in stock held, and stock MUST never go below zero.
- **FR-023**: The invariant in FR-022 MUST hold at the instant each payment returns. Stock MUST be reduced before the receipt is sent, so there is no window in which a shopper has been given a receipt for units the inventory service still believes are on the shelf. Only reporting may lag.
- **FR-024**: Two shoppers paying at the same time for the same item MUST NOT lose either reduction.

**Evidence**

- **FR-025**: The submission MUST include two load-client report files, from the default and stress parameter sets used by the previous implementations.
- **FR-026**: The submission MUST state what was verified and what was assumed, and MUST report any measured regression against the previous implementations rather than omitting it.
- **FR-027**: The submission MUST report how often the coordination had to undo a partially applied payment during the load runs, including zero.

### Key Entities

- **Catalogue item**: something the shop sells. Has an identifying code, a name and a price. Never changes while the system is running, which is what makes holding more than one copy of it safe.
- **Stock level**: how many units of an item remain. Reduced when a basket is paid for; read when reporting what is running low.
- **Basket**: one shopper's visit to a station. Has a state of open, paid or abandoned, a running count and total, and the times it opened and closed.
- **Basket line**: one scanned unit within a basket. Records the name and price at the time of sale, so a receipt reflects what was charged.
- **Scan record**: one scanned unit, recorded separately from the basket and used only for reporting. Older records are discarded once no future report can include them.
- **Ranking**: the current most-scanned items, with their positions and counts, plus which stretch of scanning activity produced it.
- **Payment coordination record**: the progress of one payment across the stores it must change. Says which steps have been applied and which have been undone, so that an interrupted payment can be finished or reversed rather than guessed at.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A shopper can scan an item and see the updated basket without a perceptible wait, at both the normal and the heavy load levels used in previous weeks. No latency figure can fail this week on its own: the measured cost of giving each service its own store is a result to be reported and compared, not a threshold to pass.
- **SC-002**: Under the normal load level, every request succeeds. Zero errors.
- **SC-003**: Under the heavy load level, any errors are explained with evidence rather than assumed benign, and their count is reported against the previous implementation's.
- **SC-004**: After every run, stock sold equals stock reduced for every item, with no unexplained discrepancy and nothing below zero.
- **SC-005**: Each microservice can be started with every other stopped, and answers the requests it owns.
- **SC-006**: No service reads or writes a data store owned by another service. Verifiable by inspection.
- **SC-007**: A payment interrupted partway through leaves no item's stock permanently wrong, demonstrated by deliberately failing a step and then checking the invariant.
- **SC-008**: Applying the same coordination step twice produces the same result as applying it once, demonstrated by a test rather than argued.
- **SC-009**: The most-popular-items ranking is materially the same as previous implementations produced under the same load.
- **SC-010**: Someone following the written setup instructions from a clean checkout reaches a running system, with every store created and seeded, and can reproduce both reports.

## Assumptions

- **Scope.** The four existing implementations are not modified. This is an addition, in a new folder.
- **The service boundaries carry over.** The assignment says "probably based on the services you identified in the last assignment", so the three business functions are unchanged from week 5. Only their data ownership changes.
- **The entry point carries over.** The assignment explicitly permits an API gateway, and the load client can only be pointed at one address, so the existing routing entry point is kept. Measurements from week 5 show it is the system's bottleneck, and that finding will be carried into this week's analysis rather than rediscovered.
- **The contract forces payment to look synchronous.** The fixed contract returns a complete itemised receipt from the payment call, so whatever coordination happens must present a finished answer to the caller by the time that call returns. This constrains the design and is not itself a choice.
- **Catalogue data may be duplicated.** Catalogue entries never change while the system runs, so more than one service holding a copy cannot produce disagreement. This is what makes FR-007 safe.
- **Comparability.** Load parameters, starting data (2,000 SKUs at 10,000 units) and the shape of the stored data match the previous weeks, so measured differences are attributable to the architecture.
- **The schema must split, and that is permitted.** The constitution requires `db/schema.sql` to stay byte-identical across weeks "unless a week's architecture genuinely requires a change". One schema per store is exactly such a case, and the split will be documented.
- **Port allocation grows.** Previous weeks took one database port each. This week needs one per store, and the submission will record which.
- **Performance may regress, and that is accepted.** Replacing one local transaction with coordination across stores is expected to cost latency. The result is a finding to be measured and reported. The design is revisited only if the system fails outright: the stock invariant breaks, or a run cannot finish.
- **Reporting may lag.** The constitution permits analytics to lag provided the amount is stated. Inventory may not, which is why FR-023 needs settling.

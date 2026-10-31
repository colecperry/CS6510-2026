# Feature Specification: Event-Driven Self-Checkout

**Feature Branch**: `003-event-driven-services` (no per-feature branch; the git extension is not installed)

**Created**: 2026-10-30

**Status**: Draft

**Input**: User description: "Finally we're going event-based. Your requirement is to implement all across microservice communications using events. To achieve this you'll need to incorporate a message broker of your choice. RabbitMQ? Kafka? ActiveMQ? You (and your AI) can choose. Submit a URL for your repo that contains the 2 json files resulting from running the load client against your server, and an architecture diagram: with default parameters (2 points), from using stress mode, ie java -cp out Main --stations=100 --duration=120 (2 points), architecture diagram showing microservices architecture and event broker (1 point). Also my teacher wants me to swap in nginx for my gateway to see if that helps."

## Clarifications

### Session 2026-10-30

- Q: Must the receipt wait for stock to be confirmed reduced? → A: No. The receipt returns as soon as the basket is marked paid, and the stock reduction follows as a message the broker guarantees to deliver. Stock becomes eventually consistent rather than exact at the instant of sale, so Principle III is amended for this week and the amendment is recorded in the analysis. Blocking on a reply was rejected because it rebuilds a synchronous call out of asynchronous parts, adds broker latency to the critical path, and leaves the broker earning nothing. The trade being bought is the ability to keep selling while the inventory service is down.
- Q: What happens to the question checkout currently asks inventory during recovery? → A: It is designed away rather than translated. No service asks another a question and waits for an answer. Because the broker now guarantees the stock reduction will be delivered, checkout no longer needs to discover whether one landed, and the recovery query disappears along with the compensation it existed to drive. A request and reply pair over the broker was rejected as a direct call wearing a costume.
- Q: Does the hand-written gateway stay? → A: Both edges are kept and selectable, and four load runs are performed rather than two. The two graded reports come from the reverse proxy, because that is the architecture being submitted. Two further reports come from the hand-written gateway so the effect of the edge change can be stated as a separate number from the effect of the communication change, which is what Principle II requires and what the question "does it help?" actually asks for.

## User Scenarios & Testing *(mandatory)*

The users here are the course grader, who reads the submission and runs the load client, and the student maintaining seven parallel implementations of one system.

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

### User Story 2 - Services talk only through the broker (Priority: P1)

No service calls another service directly.
Where one service needs another to do something, or needs to know that something happened, the information travels as a message through a broker that both of them connect to.

**Why this priority**: This is the assignment.
The previous week's services each owned their own store but still spoke to each other over direct calls, which meant a payment could not proceed unless the service it depended on was reachable at that instant.
Routing those exchanges through a broker is the change being measured.

**Independent Test**: Inspect every service's outbound configuration and confirm none holds the address of another service.
Observe the broker while the load client runs and confirm every cross-service exchange appears there.

**Acceptance Scenarios**:

1. **Given** all services are running, **When** their configuration is examined, **Then** no service holds a network address for another service.
2. **Given** the load client is running, **When** the broker is inspected, **Then** messages flow for every cross-service interaction the system performs.
3. **Given** a service is stopped and later restarted, **When** it reconnects, **Then** messages addressed to it while it was down are delivered rather than lost.

---

### User Story 3 - The edge is served by a purpose-built reverse proxy (Priority: P2)

The single address the load client points at is served by a dedicated reverse proxy rather than by application code written for the purpose.

**Why this priority**: A previous week measured the hand-written gateway consuming 87% of a CPU core while the service behind it sat at 53%, and showed that bypassing it raised throughput by 28%.
That finding named the gateway as the ceiling on every result since.
Replacing it tests whether the ceiling was the gateway or the architecture behind it.

**Independent Test**: Point the load client at the single address and confirm all seven endpoints behave identically to the previous week.
Run the same system twice, once behind each edge, and compare CPU consumed at the edge and throughput between the two.

**Acceptance Scenarios**:

1. **Given** the system is running, **When** the load client exercises all seven endpoints through the single address, **Then** every response shape and status code matches the previous week.
2. **Given** a load run completes, **When** the edge component's CPU usage is compared against the previous week's hand-written gateway, **Then** the difference is measured and reported.
3. **Given** a request arrives for a path no service owns, **When** it is routed, **Then** it is refused rather than forwarded.
4. **Given** a request arrives for an internal coordination path, **When** it is routed, **Then** it is not reachable from outside.

---

### User Story 4 - A service can be unavailable without work being lost (Priority: P2)

When one service is stopped, the rest of the system keeps accepting and recording work.
When that service comes back, the work it missed is carried out rather than discarded.

**Why this priority**: This is the property events buy that direct calls did not.
In the previous week a scan posted while the reporting service was down was dropped and never recovered, and a payment attempted while the stock service was down was refused outright.
If the broker does not change those outcomes, the week has added a moving part for nothing.

**Independent Test**: Stop one service, drive traffic, restart it, and confirm the work performed while it was down is reflected afterwards.

**Acceptance Scenarios**:

1. **Given** the reporting service is stopped, **When** shoppers scan items, **Then** scanning stays as fast as before and no scan is discarded.
2. **Given** the reporting service is restarted, **When** it resumes, **Then** the scans recorded while it was down are reflected in its figures.
3. **Given** the inventory service is stopped, **When** shoppers pay, **Then** every payment succeeds and a receipt is returned, which the previous week could not do.
4. **Given** the inventory service is restarted, **When** it resumes, **Then** stock falls by exactly the quantity sold while it was down, and the invariant holds.
5. **Given** a service is stopped indefinitely, **When** messages continue to accumulate for it, **Then** the system states a bounded limit beyond which messages are shed, and says which ones.

---

### User Story 5 - The submission can be graded (Priority: P1)

The repository contains the two load-client reports, an architecture diagram showing the services and the broker, and the written analysis.

**Why this priority**: These are the five graded points.
A working system that does not ship its evidence scores nothing.

**Independent Test**: Open the repository at the submitted URL and confirm each artefact is present and readable.

**Acceptance Scenarios**:

1. **Given** the submitted repository, **When** its reports folder is opened, **Then** it holds one report from the default parameters and one from 100 stations over 120 seconds.
2. **Given** the submitted repository, **When** the architecture diagram is opened, **Then** it shows every service, every data store, the broker, and which messages flow between them.
3. **Given** the submitted repository, **When** the written analysis is read, **Then** it reports throughput and latency against every previous week, states the stock invariant result, and separates what was verified from what was assumed.

---

### Edge Cases

- What happens to a payment when the broker itself is unreachable at the moment the shopper pays?
- What happens when a message is delivered twice, which a broker that guarantees at-least-once delivery will do?
- What happens when a service receives a message describing work it has already done?
- What happens when a service crashes after acting on a message but before acknowledging it?
- What happens when a service crashes between marking a basket paid and the stock reduction reaching the broker?
- What happens when a shopper buys an item whose recorded stock has not yet come down from an earlier sale?
- What happens when an item is sold past zero, and does the contract have any way to say so?
- How long may the reporting figures lag behind the scans they describe, and is that bound stated?
- How long may stock lag behind the sales that reduced it, and is that bound stated?
- What happens when messages accumulate for a service that never comes back?
- What happens when two shoppers buy the last unit of the same item at the same moment?

## Requirements *(mandatory)*

### Functional Requirements

**The contract**

- **FR-001**: The system MUST expose the same seven endpoints as every previous week, with identical request shapes, response shapes, field names and status codes.
- **FR-002**: The load client MUST NOT be modified, and MUST reach the system at a single address.

**Communication**

- **FR-003**: Every exchange between two services MUST travel through the message broker. No service may hold the network address of another service.
- **FR-004**: The broker MUST be a separate deployable component with its own container, started and stopped independently of the services.
- **FR-005**: Each service MUST remain independently deployable, startable and stoppable on its own, and MUST keep its own data store that no other service reaches.
- **FR-006**: A message that is delivered more than once MUST NOT cause its effect to be applied more than once.
- **FR-007**: A message MUST NOT be acknowledged until the work it describes is durably recorded, so that a crash mid-handling causes redelivery rather than loss.
- **FR-008**: A payment MUST return its receipt as soon as the basket is marked paid, without waiting for stock to be reduced. The stock reduction MUST follow as a message, and stock MUST become correct without further intervention once that message is delivered.
- **FR-009**: Marking a basket paid and recording the message that reduces its stock MUST happen together or not at all, so that a crash can never leave a paid basket whose stock reduction was never sent. Publishing to the broker MUST NOT be the thing that makes this durable, because the broker is a second system and a write to two systems is not atomic.
- **FR-010**: No service may ask another service a question and wait for an answer. Where one service needs to know something another knows, that knowledge MUST arrive as a published message.
- **FR-011**: The system MUST state the window during which stock may be stale, and MUST show that the window closes without operator action.

**The edge**

- **FR-012**: The single public address MUST be served by a dedicated reverse proxy rather than hand-written application code, and the two graded load runs MUST be performed against it.
- **FR-013**: The hand-written gateway MUST be kept and MUST remain startable in place of the reverse proxy, so the same system can be measured with each edge.
- **FR-014**: Switching between the two edges MUST change nothing else about the running system, so that a difference between their runs has exactly one possible cause.
- **FR-015**: Each edge MUST route only the seven public paths, and MUST NOT expose any internal coordination path.

**Correctness**

- **FR-016**: For every item, initial stock minus final stock MUST equal the units sold in completed transactions, and stock MUST never go negative. This MUST be checked after every load run, once outstanding messages have been delivered, and the result reported.
- **FR-017**: Any check that guards a state change MUST live inside the statement that performs it.
- **FR-018**: The system MUST state how far the reporting figures may lag behind the scans they describe.
- **FR-019**: The system MUST state a bounded limit on undelivered messages, and say what is shed when that limit is reached.
- **FR-020**: Because stock is no longer reduced before the receipt returns, the system MUST state whether an item can be sold past zero, and MUST behave the same way every previous week did rather than inventing a new response the contract does not define.

**Evidence**

- **FR-021**: The repository MUST contain two graded load-client reports from the reverse proxy, from the default parameters and from 100 stations over 120 seconds.
- **FR-022**: The repository MUST contain two further reports from the hand-written gateway at the same two parameter sets, kept separate from the graded pair so the grader cannot mistake which is which.
- **FR-023**: The repository MUST contain an architecture diagram showing every service, every data store, the broker, and the messages that flow between them.
- **FR-024**: The repository MUST contain a written analysis comparing throughput and latency against every previous week, stating the invariant result, reporting the edge effect and the communication effect as separate numbers, and separating what was verified from what was assumed.
- **FR-025**: The written analysis MUST record the amendment to Principle III and the reasoning behind it, rather than leaving the deviation silent.
- **FR-026**: Every figure quoted in a write-up MUST be checked against the reports rather than recalled, mechanically where practical.

### Key Entities

- **Event**: A statement that something happened, published by the service it happened in, carrying enough detail for any interested service to act without asking a question back. Carries an identifier that lets a repeated copy be recognised.
- **Command**: A request for a named service to do something, sent when the sender needs the work done rather than merely announcing news. Also carries an identifier that makes a repeat harmless.
- **Broker**: The component that holds messages between services, keeps them while a recipient is unavailable, and delivers them when it returns.
- **Consumer position**: What a service has already handled, kept so that redelivery after a crash does not repeat work.
- **Pending message record**: A message written into the sending service's own store in the same breath as the change that caused it, held until it has reached the broker. It exists because a service that wrote to its database and then published would have written to two systems with no way to keep them in step.
- **Basket**: A shopper's in-progress transaction, owned by the selling service.
- **Stock**: The quantity of each item available, owned by the inventory service, changed only in response to a message.
- **Scan stream**: The record of items scanned, owned by the reporting service, used to build the popularity ranking.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: All four load runs complete with a zero error rate.
- **SC-002**: The stock invariant holds exactly for all 2,000 items after every run, with no item negative, once outstanding messages have been delivered.
- **SC-003**: Stock catches up with sales within a stated and measured time of the last payment, with no operator action.
- **SC-004**: A shopper's scan is acknowledged no slower than in the previous week, measured at the 95th percentile.
- **SC-005**: Paying is acknowledged faster than in the previous week at the 95th percentile, since it no longer waits on another service. If it is not, the reason is measured rather than guessed.
- **SC-006**: Throughput is reported against all six previous weeks, with any regression stated with its magnitude rather than explained away.
- **SC-007**: Stopping the inventory service for the duration of a run does not refuse a single payment, and stock is correct once it restarts.
- **SC-008**: Stopping the reporting service for the duration of a run loses no scans, and the figures catch up within a stated time of it restarting.
- **SC-009**: The effect of the edge change is reported as a separate number from the effect of the communication change, each derived from its own pair of runs.
- **SC-010**: Every graded artefact is present at the submitted URL: two reports, one diagram, and the analysis.
- **SC-011**: Any service can be stopped and restarted on its own without restarting another.

## Assumptions

- The week begins as a copy of the microservices week, verified byte-identical before any change, because that week already has one store per service and the split along business function that this week keeps.
- The three business services stay as they are: selling, inventory and reporting. The assignment changes how they talk, not what they are.
- The broker runs in its own container on a host port not used by any previous week, alongside the three database containers.
- The broker is treated as infrastructure rather than as a service, so one broker serving all three services does not count as shared state in the way a shared database would. This is worth defending in the analysis rather than assuming silently.
- Starting data stays identical to every previous week: 2,000 items, 10,000 units each, the same price formula.
- Load parameters stay identical: the default set, and 100 stations over 120 seconds. Four runs are performed rather than two, because each edge is measured at both parameter sets.
- Principle III of the constitution, requiring inventory to be exact, is amended for this week to require inventory to be eventually exact with a stated and measured bound. The amendment is deliberate, is the point of the week, and is recorded in the analysis rather than left silent. The invariant itself is unchanged: it is checked once outstanding messages have drained.
- Selling past zero stays possible and stays floored at zero, exactly as in every previous week, because the contract defines no out-of-stock response and inventing one would break Principle I.
- The saga, its compensation and its startup recovery are expected to dissolve rather than move, because a durable message that the broker will deliver replaces the thing they existed to repair. If they turn out to still be needed, that is a finding worth reporting.
- Dependency versions stay pinned exactly as in previous weeks, so a latency difference cannot come from a library upgrade.
- The edge component's routing table is the public surface, so internal coordination paths are unreachable from outside by being absent from it.
- "All cross-microservice communication" is taken to mean service to service. The hop from the edge to a service is the client's own request being routed, not two services talking, and stays as it is.
- No authentication, authorisation or multi-tenancy is in scope, as in every previous week.

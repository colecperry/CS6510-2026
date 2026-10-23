# Phase 0 Research: Microservices Self-Checkout

Decisions taken before any code, each with the alternative that was rejected.
Background terms are defined where they first appear, because this week introduces several that the previous weeks did not need.

**Background: what a saga is.**
A database transaction gives all-or-nothing across many writes, but only inside one database.
Once the writes land in different databases there is no such thing.
A saga is the replacement: a sequence of local transactions, each in one database, plus a compensating action for each one that can undo it if a later step fails.
It does not give you atomicity. It gives you a way back.

---

## D1. Orchestration, not choreography

**Decision**: Checkout drives the payment sequence directly, calling inventory and issuing compensation itself.

**Rationale**: The contract returns a complete itemised receipt from the payment call, so the caller must be given a final answer before that call returns.
Choreography, where services publish events and react to each other, would still have to block the request while waiting for an event to come back, which is the awkward case for that pattern rather than its strength.
Orchestration also puts the whole sequence in one readable file, which matters for a submission someone else has to grade.

**Alternatives considered**: Choreography over a message broker. Rejected because it adds a broker as a new moving part and a new failure mode, buys loose coupling this system does not need at three services, and makes the flow impossible to read in one place. What it would have changed goes in `QUALITY-ATTRIBUTES.md` instead of being built.

---

## D2. Reduce stock before marking the basket paid

**Decision**: The saga order is: claim the basket, reduce stock in inventory, then mark the basket completed.

**Rationale**: Both orders have a crash window, but they fail in different directions.
Marking paid first and crashing leaves a basket that claims it was paid for with stock untouched, so the shop has sold something and still believes it is on the shelf.
Reducing stock first and crashing leaves stock low with no completed sale, so the shop is pessimistic about what it has.
Neither is acceptable permanently, which is why D4 exists, but the pessimistic one cannot oversell while it waits to be repaired.

**Alternatives considered**: Mark paid first, compensate by reverting to open. Rejected for the overselling direction above. A two-phase commit across both databases was rejected because it holds locks in two places across a network round trip, which is precisely the cost this system cannot afford on its slowest operation.

---

## D3. The in-flight state cannot be a transaction status

**Decision**: A basket being paid for keeps the wire status `OPEN`. The claim is recorded in a `paying_since` column that is never serialised, and the saga's progress lives in a separate `payment_saga` table.

**Rationale**: The contract fixes the status to `OPEN`, `COMPLETED` or `CANCELLED` (`spec/self-checkout-openapi.yaml:284`).
Adding a fourth value would be visible on `GET /transactions/{id}` and would break Principle I.
A separate column solves it: the basket is still honestly open, because the payment has not completed, and nothing new appears on the wire.

The column also has to do a second job.
Once the receipt lines have been computed, a further scan must not land in that basket, or the receipt and the stock reduction would disagree.
So the scan guard becomes `status = 'OPEN' AND paying_since IS NULL`, which closes the basket to scans the instant payment starts.

**Alternatives considered**: A fourth status value, rejected as a contract violation. Holding the claim only in the saga table, rejected because the scan path would then need a second query to find out whether a payment was in flight, which is a read-then-write across shared data and forbidden by Principle III.

---

## D4. A durable saga record, repaired at startup

**Decision**: Checkout writes a `payment_saga` row before calling inventory and updates it after. Recovery runs once when checkout starts, and can also be triggered by hand. It is not on a timer.

**Rationale**: Compensation handles the case where a step returns an error.
It does nothing for the case where checkout dies between reducing stock and marking the basket paid, because the code that would have compensated no longer exists.
Without a durable record there is no way afterwards to tell a payment that never started from one that got half way, and the invariant in FR-022 would be permanently broken by a single badly timed crash.

Recovery asks inventory whether a given saga was applied, then either finishes the payment or restores the stock.
This is the only part of the design that exists purely for failures nobody will see during a benchmark, and it is the part the assignment is actually about.

Running it at startup rather than on a timer is a deliberate trim.
Startup covers the case that matters here and that a reader will ask about: the process died mid-payment, and the books must be correct once it comes back.
A timer would additionally cover a saga stranded while checkout stayed alive, which needs inventory to be unreachable for longer than the retry budget, and which the manual trigger handles without a background task running during a measured run.

**Alternatives considered**: A periodic background sweep, rejected as more machinery than this comparison needs: it adds a tick interval to tune and another task competing for CPU during the load runs, for a failure mode strictly rarer than the crash case. No recovery at all, rejected because it makes FR-022 true by luck, and because a submission claiming correct distributed coordination needs to survive the question "what if it dies here". A workflow engine such as Temporal, rejected because hand-rolling is the point of a course on architecture, though the write-up should say that a production system would not.

---

## D5. Idempotency keyed on the saga id

**Decision**: Inventory keeps an `applied_saga` table. Reducing stock inserts the saga id and applies the decrement in one local transaction; a duplicate insert conflicts and the decrement is skipped.

**Rationale**: Retries are required by FR-013, and recovery in D4 can re-send a step it is unsure about.
Any of those can deliver the same instruction twice.
Without a key to deduplicate on, the second delivery takes stock down again and the invariant breaks in the direction that looks like theft.
Doing the insert and the decrement in one statement keeps the guard inside the write, as Principle III requires.

**Alternatives considered**: Comparing expected and actual stock levels to guess whether a step had already run. Rejected as a read-then-write, and unreliable under concurrency, which is exactly when retries happen.

---

## D6. The catalogue is replicated to all three stores

**Decision**: Every store holds its own copy of the 2,000 catalogue rows, seeded identically. Inventory remains the owner of record.

**Rationale**: Checkout needs a name and price on every scan and on every receipt line.
Analytics needs names for the ranking.
Neither may read inventory's store.
The only alternatives are replication or a network call, and the catalogue never changes while the system runs, so the usual objection to replication (the copies drift apart) cannot occur here.

**Alternatives considered**: Checkout calling inventory for pricing on each scan. Rejected because scanning runs about 1,300 times a second and is the operation the previous weeks worked hardest to keep fast; adding a network hop there would swamp the saga's cost and make the week's headline number meaningless. Passing name and price in the scan event to analytics was also considered and rejected as a schema change to `scan_log` for no gain over replication.

---

## D7. Scans reach analytics as fire-and-forget batches

**Decision**: Checkout keeps week 5's in-process queue and batching writer, but the writer now POSTs batches to analytics instead of inserting into a shared table.

**Rationale**: FR-014 says recording a scan must not delay the shopper, and week 5 already solved that with a bounded queue and a background writer.
The only change needed is the destination.
Keeping the structure means the scan path's latency should be comparable week to week, which protects the comparison.

A full queue still drops and counts, as before.
Losing a scan costs the ranking a little accuracy; blocking costs a customer their checkout.

**Alternatives considered**: Analytics polling checkout for new scans. Rejected because it inverts the dependency, making analytics need checkout to be up, and because checkout would have to retain scan data it has no other use for.

---

## D8. Retry budget sized against the client's timeout

**Decision**: The stock call retries briefly with a short backoff, with the total attempt budget held well under the load client's ten-second per-request timeout.

**Rationale**: FR-013a requires a refused payment to come back as a refusal, not as a hung request.
A refusal is a recorded, explainable 409; a request abandoned by the client is just an error in the report with no server-side evidence, which is the situation the previous weeks spent effort investigating.
The budget is set in config, not hard-coded, so the experiment can be re-run with it changed.

**Alternatives considered**: Unbounded retry until success. Rejected because it converts a dead dependency into a hung request and then into a client-side timeout, which is strictly worse evidence.

---

## D9. Separate ports, separate containers

**Decision**: Three Postgres containers on host ports 5437, 5438 and 5439, named for their owners.

**Rationale**: Separate schemas in one Postgres would share a process, a page cache and a connection limit, so a latency result could not distinguish "the architecture cost this" from "three services contended for one database process".
It would also leave cross-store queries technically possible, reducing FR-004 to an honour system.

**Alternatives considered**: One container with three databases inside it, rejected above. Three containers with one shared connection pool, rejected because a pool that can reach all three stores is the coupling being removed.

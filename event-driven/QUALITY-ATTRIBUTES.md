# Quality Attributes and Architectural Characteristics

Week 7: the services stop calling each other and talk through a message broker instead, and nginx replaces the hand-written Python gateway at the edge.

Two changes in one week, which the project's own rules say to avoid.
The answer was not to do one of them, but to measure both: four load runs, two parameter sets against each edge, so the events and the edge can be told apart.
Every number below comes from `reports/`, `reports/comparison/` or `reports/resilience-scenarios.md`.

---

## Part 1: What events changed

### The saga dissolved, and that is the week's real content

Week 6's payment spanned two services.
Checkout called inventory over HTTP to reduce stock, then marked the basket paid in its own database.
Two commits in two systems with a network call in between, which cannot be made atomic, so it needed machinery to repair the cases where only one half happened:

| File                                   | What it was for                                                               |
| -------------------------------------- | ----------------------------------------------------------------------------- |
| `checkout/app/saga/coordinator.py`     | Ran the two steps in order and compensated when the second failed             |
| `checkout/app/saga/recovery.py`        | At startup, found payments caught in the middle and finished or reversed them |
| `checkout/app/data/saga_repo.py`       | The `payment_saga` table recording how far each payment had got               |
| `inventory/app/api/internal_routes.py` | `/internal/stock/reduce` and its compensating undo                            |

All four are deleted.
Nothing replaced them.

Every one of them existed to answer a single question: **did the stock reduction happen?**
A message the broker guarantees to deliver removes the question.
Checkout writes "this sale happened" into its own database in the same transaction that marks the basket paid, and delivery is then somebody else's problem and can be retried forever.

The basket's second lock went with it.
Week 6's scan statement had to guard on `paying_since IS NULL` as well as the status, because paying spanned two transactions and left a window the status alone could not cover.
Paying is one transaction now, so the status is enough, and `checkout/app/data/baskets_repo.py` is one condition shorter.

### The dual-write problem, and why the outbox is the answer

A sale has to do two things that must either both happen or neither: mark the basket paid, and ensure stock comes down.
Those live in a database and a broker, two systems, which cannot be committed together.

- **Commit the basket, then publish.** The process dies in between: the sale is never announced, and stock stays on the shelf forever for something that was sold.
- **Publish, then commit the basket.** The process dies in between: stock comes down for a sale that was never made. The items are physically still on the shelf, but the system believes they are gone, so real inventory becomes unsellable.

Writing the message into the sender's own database, inside the same transaction, turns two commits into one.
After that there is nothing left to lose, only something left to deliver.

`checkout/tests/test_outbox.py` proves both halves against a real database: a paid basket always has its message, and a failed message write always leaves the basket open.

### At least once, applied exactly once

No broker delivers exactly once.
RabbitMQ promises at least once, which means a message may arrive twice after a network blip or a consumer crash.

Inventory makes that harmless by keying on the message id and letting the insert be the check:

```sql
INSERT INTO applied_requests (message_id, lines)
VALUES ($1, $2::jsonb)
ON CONFLICT (message_id) DO NOTHING
RETURNING message_id
```

A row comes back: this copy is the first, so stock moves.
Nothing comes back: this is a repeat, so nothing happens.
There is no window between looking and acting, which satisfies the project's rule that a guard must live inside the statement it guards.

This is the same mechanism week 6 used, re-keyed.
Week 6 keyed on a request id checkout invented for the call; this week keys on the outbox row's own id, which is the thing that travels with the message.

### Choreography, not orchestration

Week 6 was orchestration: checkout knew that paying meant reducing stock, and told inventory to do it.

This week is choreography.
Checkout announces `sale.completed` and stops.
Inventory decides for itself that a completed sale means stock comes down.
Checkout does not know inventory exists, would not know if it refused, and nothing in checkout would change if a fourth service started listening to the same message.

The cost is that nothing in the system states the whole flow in one place.
Understanding what a payment does means reading the publisher and then separately finding whoever subscribes, which is what `ARCHITECTURE.md` exists to compensate for.

---

## Part 2: What the edge changed

### The gateway was the bottleneck, and now that is proven rather than suspected

Weeks 5 and 6 both named the Python gateway as the probable ceiling.
Neither could show it, because there was nothing to compare against.

This week there is.
The two event-driven columns in the README differ only in which process holds port 8080.

|                           | Python gateway  | nginx | Change           |
| ------------------------- | --------------- | ----- | ---------------- |
| Transactions/sec, default | 37.8            | 98.7  | +161%            |
| Transactions/sec, stress  | 39.3            | 168.2 | +328%            |
| Edge CPU, default         | 86.5% of a core | 8.9%  | about one tenth  |
| Edge CPU, stress          | 94.7% of a core | 11.6% | about one eighth |

The same traffic, the same three services behind it, the same routing table.
One edge saturates a core and caps the system at roughly 39 transactions per second; the other uses a tenth of a core and the cap moves somewhere else.

**168.2 transactions and 1,767.1 items per second under stress is the fastest result in the series**, past the pipeline's 124.5 and 1,313.7, in the week with the most processes and the most network hops.

### Checkout is now the ceiling

With nginx at the edge, checkout runs at 99.5% of a core under stress while inventory sits at 4.8% and analytics at 14.8%.

That is the next thing to fix, and the architecture makes the fix available: checkout is stateless with respect to the broker, so a second copy would take half the traffic and the durable quorum queues would share the work.
Week 6 could not do that so cleanly, because the saga coordinator's recovery pass assumed one process owned the repair.

---

## Part 3: What it cost

### Payment is eventually consistent, which amends Principle III

The project's constitution says:

> Inventory MUST be exact. Analytics MAY lag, and the amount it may lag MUST be stated.

That is amended this week to: **inventory may also lag, and the amount is stated below.**

A receipt is returned before stock has moved.
For a measured median of 48.8 ms the system has sold something it has not yet taken off the shelf.

The invariant itself is unchanged and still holds exactly.
`scripts/check_invariant.sh` waits for the outbox to drain and then compares units sold against units gone, across both databases, and all four runs came back with zero unexplained violations and announced sales exactly matching applied ones.

**Why the amendment was taken rather than avoided.**
Keeping inventory exact at the moment of payment requires the shopper to wait for inventory, which is the coupling this week exists to remove.
Week 6 shipped that version and its cost is on record: an inventory outage returned HTTP 503 to the shopper, a failed sale caused by a service the shopper was not buying from.
Choosing a 48.8 ms window over a failed sale is the same trade the constitution already makes for analytics, applied one table further along.

**The honest regression: under saturation the window is seconds, not milliseconds.**
At default load the outbox was empty the instant the run ended.
Under stress it took 48 seconds to drain after the run finished.

The cause is measured, not guessed: checkout runs at 99.5% of a core under stress, and the relay shares an event loop with the request handlers, so it gets starved exactly when there is most to deliver.
The fix is to move the relay into its own process, which costs a deployment unit and was not worth doing this week.

Stated plainly, so nobody has to find it out: **this system's stock figure can be up to roughly a minute stale while it is saturated.**

### Correctness is still not enforceable by the database, and now it is further away

Week 5 and earlier checked the invariant with one SQL query, because units sold and units remaining lived in the same place.
Week 6 split them across two databases, so the check became a program that reads both.

This week adds a second complication.
A difference between the two stores is now ambiguous: it may mean something is wrong, or it may mean a message is still in flight.
The check has to wait for the outbox before it can compare anything at all, which means **there is no instant at which the system can be asked whether it is correct and answer immediately.**

That is a real loss and it is inherent to the style, not to this implementation.

### New failure modes

| Failure                       | What happens                                              | Covered by                                                    |
| ----------------------------- | --------------------------------------------------------- | ------------------------------------------------------------- |
| Broker unreachable at publish | The row stays unsent and the relay retries with a backoff | `test_relay_retry.py`, scenario 2                             |
| Broker restarts               | Sales survive, queued scans do not                        | Not exercised; stated as the known limit                      |
| Message delivered twice       | Inventory recognises the id and does nothing              | `test_idempotency.py`                                         |
| Consumer crashes mid-handling | Not acknowledged, so redelivered                          | Manual ack in `common/bus.py`                                 |
| Relay dies                    | Stock stops moving and nothing says so                    | Mitigated by catching everything in the loop, not by alerting |
| Checkout saturated            | The relay is starved and the lag grows to seconds         | Measured: 48s under stress                                    |

The last two are the ones that would bite in production.
A relay that stops delivering looks exactly like a working system until someone notices stock has not moved, and nothing here would tell them.

### A deviation: nginx is installed natively, not containerised

Every week so far has been self-contained: `docker compose up -d` and `uv sync` and nothing else.
This week needs nginx installed on the machine.

That was chosen over comparability's alternative deliberately.
A container on macOS reaches the host through a network translation layer that a host process on loopback does not pay.
Putting one edge behind that and not the other would have measured Docker rather than the edge, and the edge comparison is most of what this week found.

The cost is one extra install step, documented in the README with the exact version tested.

### Complexity, honestly

|                                    | Week 6                                    | Week 7                                           |
| ---------------------------------- | ----------------------------------------- | ------------------------------------------------ |
| Processes to run                   | 4                                         | 4, plus a broker                                 |
| Containers                         | 3                                         | 4                                                |
| Things that can be separately down | 7                                         | 8                                                |
| Files in `checkout/app/`           | 13                                        | 11                                               |
| Concepts a reader must hold        | saga, compensation, recovery, idempotency | outbox, relay, exchange, queue, ack, idempotency |

The broker is a whole extra piece of infrastructure, with its own durability settings, its own queue types and its own failure behaviour.
Against that, checkout got smaller and the hardest thing in it last week, a distributed transaction repaired by hand, is gone.

It is not obviously a simplification.
It is a different set of things to understand, and the honest claim is that they are easier things.

---

## Part 4: Characteristics, revisited

| Characteristic | Week 6   | Week 7       | Why                                                                         |
| -------------- | -------- | ------------ | --------------------------------------------------------------------------- |
| Availability   | Poor     | **Strong**   | Three outages survived with nothing lost and no shopper affected            |
| Performance    | Poor     | **Strong**   | Fastest in the series under stress, once the edge stopped being the ceiling |
| Scalability    | Moderate | **Strong**   | Checkout is the bottleneck and can now be run twice                         |
| Consistency    | Strong   | **Moderate** | Stock is 48.8 ms behind, and up to 48 seconds behind under saturation       |
| Testability    | Moderate | **Moderate** | Easier units, harder end-to-end: no single place states the whole flow      |
| Observability  | Poor     | **Moderate** | The management UI shows queue depths, which is more than week 6 offered     |
| Simplicity     | Poor     | **Poor**     | Fewer concepts in the code, one more piece of infrastructure                |
| Coupling       | Moderate | **Strong**   | No service holds another's address, checked by reading the source           |

---

## What the measurements showed

1. **The edge was the ceiling all along.** Swapping one process more than quadrupled stress throughput. Two previous weeks suspected this; this is the first week that could show it.
2. **Events help most under load.** Holding the edge fixed, throughput fell 7.8% at default and rose 41% under stress. Removing a blocking call matters when there is contention and costs a little when there is not.
3. **The saga was not worth what it cost.** Four files of repair machinery, replaced by one table and a loop, with better availability than before.
4. **Scans have not changed shape since week 4.** 10.72ms p95 this week against 7.10ms then, through two more hops and a broker, because the design has always been queue it and move on.
5. **Nothing was lost in any outage.** Including the one case week 6 explicitly accepted losing: scans published while analytics was down.
6. **The lag is bounded by CPU, not by design.** 48.8 ms when there is headroom, 48 seconds when there is not.

---

## What was verified, and what was assumed

**Verified by running it:**

- All four load runs, zero errors across 417,021 operations.
- The invariant after every one of them, zero unexplained violations, announced sales exactly matching applied.
- All three outage scenarios, start to finish, with the commands in `reports/resilience-scenarios.md`.
- The 48.8 ms median lag, over ten sales, with `scripts/measure_lag.sh`.
- The 48 second drain under stress, printed by `check_invariant.sh`.
- CPU for every process in every run, with `scripts/measure_cpu.sh`.
- Both edges returning byte-identical responses across all seven endpoints and all six error codes, by diffing two smoke-test runs.
- The boundary checks being non-vacuous, by planting a violation of each of the five in turn and confirming each one failed.
- The broker's topology, read from its own management API rather than from the code that asked for it.

**Assumed, or reasoned rather than measured:**

- The claim that a second copy of checkout would lift the ceiling. Not tried.
- The disk bound on the sale backlog, which is the broker's configured limit and arithmetic rather than a filled disk.
- The 28 second scan-shedding figure, which is the queue size divided by the measured scan rate, not a reproduced 28 second outage.
- That a broker restart loses queued scans. It follows from publishing them transiently and was not exercised.
- The attribution of the stress lag to event-loop starvation. The CPU figure supports it and nothing else explains it, but the relay was not profiled.

**Not done:**

- No alerting on a dead relay. Named as a failure mode, not addressed.
- The same four runs were not repeated to check variance. Each figure is one run.

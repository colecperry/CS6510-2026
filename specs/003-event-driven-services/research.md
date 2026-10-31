# Research: Event-Driven Self-Checkout

Phase 0 output.
Eleven decisions, each with what was chosen, why, and what was turned down.

---

## D1: The broker is RabbitMQ

**Decision**: RabbitMQ 4.x, in its own container, reached from Python with `aio-pika`.

**Rationale**: The workload is a work queue, not a replayable log.
Checkout hands inventory a piece of work and wants it done exactly once; it never wants to read last Tuesday's sales again.
RabbitMQ is built for that shape, acknowledges messages one at a time, and runs as a single container with nothing else to stand up.

`aio-pika` is chosen over raw `pika` because every service is already asyncio, and `pika`'s blocking connection would need a thread per service to avoid stalling the event loop.

**Alternatives considered**:

- **Kafka**: a replayable log, which genuinely suits the scan stream. Rejected because it needs KRaft or ZooKeeper, wants far more memory than three Postgres containers combined, and brings partitions and consumer groups that three services do not need. The per-message acknowledgement in FR-007 maps onto RabbitMQ directly and onto Kafka only through offset management.
- **ActiveMQ Artemis**: capable and a fine choice. Rejected for having no advantage here while adding a JVM to a machine already running Postgres three times and the Java load client.
- **Redis Streams**: lightest of all. Rejected because consumer groups there do not give the "do not acknowledge until the work is durably recorded" guarantee as cleanly, and FR-007 depends on exactly that.

---

## D2: Two message paths, with different guarantees

**Decision**: Sales and scans travel separately and are protected differently.

| Path | Message          | Durability                             | May it be lost? |
| ---- | ---------------- | -------------------------------------- | --------------- |
| Sale | `sale.completed` | Outbox, persistent, publisher confirms | No              |
| Scan | `items.scanned`  | Direct publish, transient, droppable   | Yes             |

**Rationale**: Principle III already draws this line: inventory must be exact, analytics may lag.
Giving both paths the same protection would either make scans expensive or make sales unsafe.
Paying for durability only where money is involved is the whole reason the principle distinguishes them.

**Alternatives considered**: one uniform path for both. Rejected because it would put the outbox and publisher confirms on the hot scan path, 1,300 times a second, to protect data the system is explicitly allowed to lose.

---

## D3: Checkout publishes through a transactional outbox

**Decision**: Marking a basket paid and recording the outgoing `sale.completed` message happen in one transaction against checkout's own database.
A background relay then reads unsent rows, publishes them with publisher confirms, and marks them sent.

**Rationale**: This is the one genuinely hard problem the week creates, and it is the same problem week 6 had in a new place.
Checkout's database and the broker are two systems. A write to two systems is not atomic, so whichever order you choose, a crash in the middle loses something.

- Commit, then publish: crash in between and the basket is paid with stock never reduced. Money lost.
- Publish, then commit: crash in between and stock is reduced for a sale that never happened. Money lost the other way.

The outbox removes the dual write entirely. There is only ever one commit, to one database, and the message is part of it.
Delivery becomes a separate concern that can retry forever without the shopper waiting.

This is also why the broker being unreachable no longer refuses a payment: the row is already safe, and the relay catches up.

**Alternatives considered**:

- **Publish inside the transaction and hope**: the naive version, rejected above.
- **Two-phase commit across Postgres and RabbitMQ**: technically possible, rejected for holding a prepared transaction open across a network round trip, which is the thing week 6 already rejected 2PC for.
- **Change data capture from the write-ahead log**: no dual write and no relay polling, but it means running Debezium and Kafka Connect, which is more infrastructure than the rest of the week combined.

---

## D4: Inventory stays idempotent, unchanged

**Decision**: Inventory keeps its `applied_requests` table and its insert-as-check, keyed now on the outbox row's message id rather than a saga id.

**Rationale**: At-least-once delivery means a message will sometimes arrive twice: the relay can publish, crash before marking the row sent, and publish again on restart.
The existing guard already handles exactly this, because it was written for exactly this.

```sql
INSERT INTO applied_requests (message_id, lines)
VALUES ($1, $2::jsonb)
ON CONFLICT (message_id) DO NOTHING
RETURNING message_id
```

The insert is the check, so there is no window between asking and acting, and two copies racing cannot both win.

**Alternatives considered**: relying on the broker for exactly-once delivery. Rejected because no broker offers it end to end across a consumer's own database write; what they offer is at-least-once plus a place for the consumer to be idempotent. This table is that place.

---

## D5: The saga dissolves

**Decision**: Delete `saga/coordinator.py`, `saga/recovery.py` and `data/saga_repo.py`, and the `payment_saga` table with them.

**Rationale**: Each of those existed to answer one question: *the stock reduction may or may not have happened, so what do I do?*
With the outbox, the reduction is guaranteed to happen eventually, so the question never arises.

- **Compensation** undid a reduction when the call returned an error. There is no call and no error to return.
- **Startup recovery** finished or undid payments interrupted by a crash. A crash now leaves an unsent outbox row, which the relay sends on restart with no decision to make.
- **The `was_applied` question** asked inventory whether a reduction landed. Nothing needs to know.

This is the most interesting result of the week and the part worth presenting: moving to events removed three files rather than adding three.

**Alternatives considered**: keeping the saga table as an audit trail. Rejected because an unused table invites the reader to assume it does something, and Principle V exists to stop exactly that.

---

## D6: The basket lock dissolves too

**Decision**: Drop the `paying_since` column.

**Rationale**: It did two jobs, and a single transaction now does both.

Payment is one transaction against one database: flip the status, read the receipt lines, write the outbox row.
The status guard on the update is what stops two payments claiming one basket, because only one transaction can move a row from `OPEN` to `COMPLETED`.
And a scan arriving mid-payment blocks on the same row until the commit, then sees `COMPLETED` and is refused.

```sql
UPDATE transactions
SET status = 'COMPLETED', completed_at = now()
WHERE transaction_id = $1 AND status = 'OPEN' AND item_count > 0
RETURNING station_id, item_count, running_total, started_at, completed_at
```

The column existed in week 6 only because the payment spanned two transactions with a network call between them, leaving a window the status alone could not cover. The window is gone.

**Alternatives considered**: keeping it for safety. Rejected as cargo cult. A guard whose reason has gone is a guard nobody will dare remove later.

---

## D7: Choreography, not orchestration

**Decision**: Checkout announces that a sale happened. It does not instruct anyone.
Inventory subscribes and decides for itself that a sale means stock comes down.

**Rationale**: Week 6 was orchestration: checkout knew the steps, called them in order, and handled their failures.
Routing the same instructions through a broker would be orchestration with extra latency.

The honest event-driven version is that checkout publishes `sale.completed` and stops caring.
Adding a fourth service later, say receipts by email, means it subscribes to the same message and checkout does not change.

This also contrasts directly with week 6's recorded decision, which rejected choreography and said what it would have changed should be written up rather than built. This week builds it.

**Alternatives considered**: commands addressed to a named service, for example `inventory.reduce_stock`. Rejected because the sender then knows who is listening, which is the coupling the broker was introduced to remove.

---

## D8: Scans keep their queue, and only change destination

**Decision**: Keep the in-process queue and the 200-item batching exactly as week 6 has them.
Replace the HTTP post at the end with a broker publish.

**Rationale**: The queue is what keeps `record()` non-blocking, and the whole scan path's latency lineage back to week 4's 7.10ms depends on it.
Changing the destination and nothing else is what lets this week's scan figure be compared to last week's at all.

Batching is kept for the same reason, and because 7 messages a second carrying 200 items each costs the broker far less than 1,300 carrying one.

**Alternatives considered**: one message per scan, published inline. Rejected because it puts a network call back on the shopper's path, which SC-004 forbids.

---

## D9: nginx runs natively, not in a container

**Decision**: Install nginx via Homebrew and run it as a host process.

**Rationale**: This one affects whether the comparison means anything.

The Python gateway runs as a host process and reaches the services over loopback.
An nginx container on macOS would reach those same services through Docker Desktop's virtual machine and `host.docker.internal`, which is a NAT hop the Python gateway never pays.
Measuring the two that way would compare nginx-plus-a-virtual-network against Python-on-loopback, and a result either way would be unattributable.

Running nginx natively puts both edges in the same position: a host process, loopback to three host services. The only difference left is the edge itself, which is the only difference we want.

This costs the project its "everything in Docker" tidiness, and the README must say how to install nginx.
That is a worthwhile trade for a measurement that survives being questioned.

**Alternatives considered**:

- **nginx container with `host.docker.internal`**: rejected above.
- **nginx container with `network_mode: host`**: would solve it on Linux. Does not work as expected on Docker Desktop for macOS, where the daemon runs inside a virtual machine, so "host" is the virtual machine and not the Mac.
- **Moving the services into containers too**: would make the comparison fair again, but changes how all six previous weeks ran and breaks the comparison that matters more.

---

## D10: Ports, and what the broker exposes

**Decision**:

| Component                      | Host port        | Note                                                               |
| ------------------------------ | ---------------- | ------------------------------------------------------------------ |
| checkout-db                    | 5440             | 5433 to 5439 belong to weeks 1 to 6                                |
| inventory-db                   | 5441             |                                                                    |
| analytics-db                   | 5442             |                                                                    |
| RabbitMQ (AMQP)                | 5672             | the default; no previous week runs a broker                        |
| RabbitMQ (management UI)       | 15672            | used to show the broker working, and to screenshot for the diagram |
| edge                           | 8080             | one edge at a time, never both                                     |
| checkout, inventory, analytics | 8081, 8082, 8083 | unchanged from week 6                                              |

**Rationale**: Principle II allocates Postgres ports one per week and never reuses them.
The management UI is worth exposing because US2 requires showing that every cross-service exchange appears in the broker, and the UI is the cheapest way to demonstrate that to a grader.

---

## D11: Four runs, and which two are graded

**Decision**: Run default and stress against each edge. The two nginx runs are the graded pair and sit in `reports/`. The two Python-gateway runs sit in `reports/comparison/`.

**Rationale**: Two changes landed in one week, so a single pair of reports cannot attribute either.
Four runs make the edge effect and the communication effect separately visible:

- nginx versus Python gateway, this week: the edge effect, which is what the teacher asked for.
- Python gateway, this week versus week 6: the communication effect, with the edge held constant.

Keeping them in different folders stops the grader having to work out which four files are which, and stops the student quoting the wrong pair into a write-up.

**Alternatives considered**: two runs with nginx only, and reasoning about the rest. Rejected because Principle IV requires an explanation offered without measurement to be labelled a hypothesis, and the whole point of the teacher's question is to get a number.

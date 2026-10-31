# Architecture: event-driven self-checkout

Week 7 of the series.
Three services, three databases, one message broker, and a choice of two edges.

The one rule that shapes everything else: **no service calls another service.**
There is no address in any service's configuration that reaches another service, and the tests in `tests/test_boundaries.py` check that by reading the source.
Everything a service needs to tell another one, it announces to the broker instead, and whoever cares subscribes.

## The diagram

```
                                              load client
                                                   |
                                                   | HTTP, one address
                                                   v
                                      +-------------------------+
                                      |     edge, port 8080     |    one of two, never both
                                      |   nginx OR gateway      |    routes by path, no logic
                                      +-------------------------+
                                                   |
                     +-----------------------------+-----------------------------+
                     |                             |                             |
               /transactions              /items, /inventory                /analytics
                     |                             |                             |
                     v                             v                             v
   +--------+  +-----------+     +--------+  +-----------+     +--------+  +-----------+
   |pg 5440 |<>| checkout  |     |pg 5441 |<>| inventory |     |pg 5442 |<>| analytics |
   |baskets |  |   :8081   |     | stock  |  |   :8082   |     |scan log|  |   :8083   |
   | outbox |  +-----------+     |applied |  +-----------+     |ranking |  +-----------+
   +--------+        |           +--------+        ^           +--------+        ^
                     |                             |                             |
     publishes both  |       from inventory.sales  |       from analytics.scans  |
                     v                             |                             |
   +-----------------------------------------------------------------------------------+
   |                  RabbitMQ, port 5672   (management UI on 15672)                   |
   |                                                                                   |
   |                    exchange "selfcheckout"   (topic, durable)                     |
   |                         |                                |                        |
   |                  sale.completed                    items.scanned                  |
   |                         v                                v                        |
   |               queue inventory.sales            queue analytics.scans              |
   |                 (durable, quorum)                (durable, quorum)                |
   |               persistent, confirmed          transient, not confirmed             |
   +-----------------------------------------------------------------------------------+
```

Every line into or out of the broker starts at a service, never at a database.
Checkout only ever publishes; inventory and analytics only ever subscribe.
Each database touches exactly one service and nothing else.
No line goes from one service to another, which is the whole claim this week makes.

## Who owns what

| Service       | Port | Database | Tables it owns                                        | Endpoints                                                                                                           |
| ------------- | ---- | -------- | ----------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------- |
| **checkout**  | 8081 | 5440     | `transactions`, `transaction_items`, `outbox`         | `POST /transactions`, `POST /transactions/{id}/items`, `POST /transactions/{id}/complete`, `GET /transactions/{id}` |
| **inventory** | 8082 | 5441     | `stock`, `applied_requests`                           | `GET /items`, `GET /inventory/low-stock`                                                                            |
| **analytics** | 8083 | 5442     | `scan_log`, `popularity_state`, `popularity_snapshot` | `GET /analytics/popular-items`                                                                                      |

Each service holds its own read-only copy of `catalog`, seeded identically, because catalogue rows never change while the system runs.
That is a duplicate, not a shared table: no service can read another's.

## The two message paths, and why they are not the same

|                                  | `sale.completed`      | `items.scanned`           |
| -------------------------------- | --------------------- | ------------------------- |
| Published by                     | the outbox relay      | the scan writer           |
| Consumed by                      | inventory             | analytics                 |
| Written to disk by the broker    | yes                   | no                        |
| Publisher waits for confirmation | yes                   | no                        |
| Survives a broker restart        | yes                   | no                        |
| Cost of losing one               | stock is wrong, money | a ranking is slightly off |

A sale is money, so it is persistent and confirmed.
A scan is a statistic, so it is transient and unconfirmed, which keeps the scan path as fast as it was in week 4.

Both queues are durable, so both survive the *consumer* being down.
Only the sale survives the *broker* being down.

## How a purchase works

1. The shopper's station opens a basket and scans items. Each scan writes a row in checkout's own database and drops the SKU into an in-memory queue. The request ends there.
2. A background writer takes up to 200 queued scans at a time and publishes them as one `items.scanned` message. Nothing waits for it.
3. The shopper pays. In **one transaction against checkout's own database**, the basket flips to `COMPLETED` and a `sale.completed` message is written into the `outbox` table. The receipt is returned. Nothing has been sent to anyone.
4. A relay polls the outbox every 50 ms, publishes each owed message, waits for the broker to confirm it, and only then marks the row sent.
5. Inventory receives the sale, records the message id and reduces stock in one transaction, and acknowledges only after that commits.
6. Analytics receives the scan batch, inserts it into its scan log, and rebuilds the ranking on its own schedule.

Measured delay between step 3 and step 5: **48.8 ms median**.

## Why the outbox exists

Step 3 has to do two things that must either both happen or neither: mark the basket paid, and ensure stock comes down.
Those live in two different systems, a database and a broker, and two systems cannot be committed together.

- Commit the basket, then publish: the process dies in between and the sale is never announced. Stock stays on the shelf forever.
- Publish, then commit the basket: the process dies in between and stock comes down for a sale that was never made. Real inventory becomes unsellable.

Writing the message into the sender's own database, inside the same transaction, turns two commits into one.
After that there is nothing left to lose, only something left to deliver, and delivery can be retried forever.

## Why there is no saga any more

Week 6 paid by calling inventory over HTTP from inside the request.
That needed a `payment_saga` table to record how far a payment had got, a compensating undo for when the second step failed, and a recovery pass at startup to finish or reverse anything caught in the middle.

All of it existed to answer one question: *did the stock reduction happen?*

A message the broker guarantees to deliver removes the question.
Four files were deleted and nothing replaced them:

- `checkout/app/saga/coordinator.py`
- `checkout/app/saga/recovery.py`
- `checkout/app/data/saga_repo.py`
- `inventory/app/api/internal_routes.py`

## Delivered at least once, applied exactly once

The broker promises to deliver a message *at least* once.
After a network blip or a consumer crash it may deliver the same one again.

Inventory makes that harmless.
Every sale carries the `messageId` from its outbox row, and reducing stock begins by inserting that id:

```sql
INSERT INTO applied_requests (message_id, lines)
VALUES ($1, $2::jsonb)
ON CONFLICT (message_id) DO NOTHING
RETURNING message_id
```

If a row comes back, this copy is the first and the stock moves.
If nothing comes back, this copy is a repeat and nothing happens.
The insert *is* the check, so there is no window between looking and acting in which a second copy could slip through.

At-least-once delivery plus an idempotent consumer is how a system gets exactly-once behaviour without exactly-once delivery, which no broker can actually provide.

## The edge

Both edges listen on 8080, route by longest matching path prefix, hold no business rules and open no database connection.

- `edge/gateway/` is the Python reverse proxy carried from week 5.
- `edge/nginx.conf` is the same routing table as configuration for nginx, run natively rather than in a container.

`./scripts/start_all.sh --edge=nginx` or `--edge=python` starts exactly one and refuses to start both.
nginx is native on purpose: a container on macOS would pay a network translation hop the Python process does not, and the comparison would measure Docker rather than the edge.

## What this costs

Payment is now **eventually consistent**.
A receipt is returned before stock has moved, so for roughly 50 ms the system has sold something it has not yet taken off the shelf.

That is a deliberate amendment to the project's own rule that a response never describes a state the database has not reached.
The alternative was to make the shopper wait for inventory, which is exactly the coupling this week exists to remove, and which week 6 already showed turns an inventory outage into a failed sale.

The invariant still holds exactly: `scripts/check_invariant.sh` waits for the outbox to drain and then compares units sold against units gone, across both databases.

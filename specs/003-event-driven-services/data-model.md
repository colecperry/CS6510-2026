# Data Model: Event-Driven Self-Checkout

Phase 1 output.
Three stores, one per service, plus the messages that travel between them.
Nothing here is shared: a service's tables are reachable only by that service.

---

## Checkout's store (port 5440)

Owns baskets, what went into them, and the messages it owes the rest of the system.

### `transactions`

A shopper's visit to a station.

| Column           | Type                   | Note                                                      |
| ---------------- | ---------------------- | --------------------------------------------------------- |
| `transaction_id` | TEXT PK                | `tx-` plus a sequence value                               |
| `station_id`     | TEXT NOT NULL          | a label on the receipt; nothing branches on it            |
| `status`         | TEXT NOT NULL          | `OPEN`, `COMPLETED` or `CANCELLED`, fixed by the contract |
| `item_count`     | INTEGER NOT NULL       |                                                           |
| `running_total`  | NUMERIC(12,2) NOT NULL |                                                           |
| `started_at`     | TIMESTAMPTZ NOT NULL   |                                                           |
| `completed_at`   | TIMESTAMPTZ            |                                                           |

**Changed from week 6**: the `paying_since` column is removed.
It existed because a payment spanned two transactions with a network call between them, leaving a window the status alone could not cover.
Payment is now one transaction, so the status guard covers it.
See [research.md](./research.md) D6.

### `transaction_items`

One row per scanned unit, carrying the name and price charged at the time.
Unchanged from week 6.

### `catalog`

A read-only copy of item names and prices.
Every service holds its own copy, because a name on a receipt must not require a call to another service.
Unchanged from week 6.

### `outbox`

New this week. The messages checkout has committed to sending but may not have sent yet.

| Column        | Type                 | Note                                                      |
| ------------- | -------------------- | --------------------------------------------------------- |
| `message_id`  | UUID PK              | travels with the message; the consumer deduplicates on it |
| `routing_key` | TEXT NOT NULL        | which message this is, for example `sale.completed`       |
| `payload`     | JSONB NOT NULL       | the message body                                          |
| `created_at`  | TIMESTAMPTZ NOT NULL |                                                           |
| `sent_at`     | TIMESTAMPTZ          | null until the broker has confirmed it                    |

Indexed on `sent_at` where null, because the relay only ever asks for unsent rows and that set is normally empty.

**Why it exists**: checkout's database and the broker are two systems, and a write to two systems is not atomic.
Writing the message into this table inside the same transaction that marks the basket paid turns two writes into one.
Delivery then becomes a separate job that can retry forever without a shopper waiting.
See [research.md](./research.md) D3.

**Removed from week 6**: the `payment_saga` table, along with the compensation and recovery it drove.
A guaranteed message replaced the thing they repaired.

---

## Inventory's store (port 5441)

Owns stock, and the record of which messages it has already acted on.

### `stock`

Quantity on hand per SKU.
Changed only by a message, never by a direct call.
Unchanged from week 6, including the floor at zero.

### `applied_requests`

The guard that makes a repeated message harmless.

| Column       | Type                 | Note                                                          |
| ------------ | -------------------- | ------------------------------------------------------------- |
| `message_id` | UUID PK              | the id carried by the message                                 |
| `lines`      | JSONB NOT NULL       | what this message moved, so it can be reversed if ever needed |
| `applied_at` | TIMESTAMPTZ NOT NULL |                                                               |

**Changed from week 6**: the key is the outbox message id rather than a saga id.
The mechanism is identical and deliberately so: the insert is the check, and the row is written in the same transaction as the stock change.

The `restored_at` column is dropped. It existed for compensation, and there is no longer anything that compensates.

### `catalog`

This service's own copy. Unchanged.

---

## Analytics' store (port 5442)

Owns the scan stream and the published ranking.
Entirely unchanged from week 6; only the way scans arrive has changed.

### `scan_log`

One row per scanned unit, with a monotonic `seq` the window arithmetic depends on.

### `popularity_state`

A single row holding the window size, the slide interval, and which stretch of the stream the published ranking covers.

### `popularity_snapshot`

The published ranking itself.

### `catalog`

This service's own copy, used to put names on the ranking without calling inventory.

---

## Messages

Two, and they are protected differently because they are worth different amounts.
Full field definitions are in [contracts/README.md](./contracts/README.md).

### `sale.completed`

Published by checkout when a basket is paid for. Consumed by inventory.

- **Durable.** Written to the outbox inside the payment transaction, published with publisher confirms, persisted by the broker.
- **At-least-once.** May arrive twice; `applied_requests` makes the second copy do nothing.
- **An announcement, not an instruction.** Checkout says a sale happened. Inventory decides for itself that this means stock comes down. A fourth service could subscribe tomorrow without checkout changing.

### `items.scanned`

Published by checkout in batches of up to 200. Consumed by analytics.

- **Transient and droppable.** No outbox, no confirms. Principle III allows analytics to lag, and putting durability machinery on a path that runs 1,300 times a second to protect data the system may lose would be the wrong trade.
- Carries the SKUs only. Analytics already holds its own catalog for names.

---

## State transitions

A basket moves through exactly two transitions, both guarded inside the statement that performs them.

```
            scan                     pay
  OPEN  ----------->  OPEN  ----------------->  COMPLETED
    |                                                ^
    |                                                |
    +--  guarded by: status = 'OPEN'  ---------------+
                                      and item_count > 0
```

There is no in-flight payment state this week.
The contract fixes `status` to three values, and week 6 needed a fourth concept which it carried in `paying_since`.
Because payment is now a single transaction, the row lock Postgres takes for the duration of that statement is the only exclusion needed.

A stock reduction moves through one transition, recorded in `applied_requests`:

```
  not seen  --- message arrives --->  applied   (any further copy: ignored)
```

# Contracts: Event-Driven Self-Checkout

Two contracts matter this week.
The HTTP one is fixed by the course and does not change.
The message one is new, and is what the assignment is actually about.

---

## 1. The HTTP contract (unchanged)

The same seven endpoints as every previous week, defined in `spec/self-checkout-openapi.yaml`.
Identical paths, request shapes, response shapes, field names and status codes.

| Method | Path                          | Service behind the edge |
| ------ | ----------------------------- | ----------------------- |
| POST   | `/transactions`               | checkout                |
| POST   | `/transactions/{id}/items`    | checkout                |
| POST   | `/transactions/{id}/complete` | checkout                |
| GET    | `/transactions/{id}`          | checkout                |
| GET    | `/items`                      | inventory               |
| GET    | `/inventory/low-stock`        | inventory               |
| GET    | `/analytics/popular-items`    | analytics               |

The one behavioural change sits behind `POST /transactions/{id}/complete`.
It returns the same receipt with the same fields, but it no longer waits for stock to be reduced before doing so.
Nothing on the wire shows this, which is the point: the contract is a control in the experiment.

**Error codes** are unchanged: `NOT_FOUND`, `INVALID_REQUEST`, `EMPTY_BASKET`, `TRANSACTION_NOT_OPEN`, `ITEM_NOT_FOUND`.

**Removed**: the `/internal/stock/*` paths week 6 used for service-to-service coordination.
There is nothing left to coordinate over HTTP, so those routes and the client that called them are deleted rather than hidden.

---

## 2. The message contract (new)

### Topology

One topic exchange, two durable queues.

```
                      exchange: selfcheckout  (topic, durable)
                                   |
         routing key               |              routing key
         sale.completed            |              items.scanned
                 |                 |                     |
                 v                 |                     v
      queue: inventory.sales       |         queue: analytics.scans
         (durable, quorum)         |          (durable, quorum)
                 |                 |                     |
                 v                 |                     v
          inventory service        |            analytics service
```

A topic exchange rather than direct queues, so a service added later subscribes to a routing key that already exists and no publisher changes.
That is the property choreography is chosen for, and a direct queue per consumer would throw it away.

### `sale.completed`

**Published by**: checkout, via the outbox relay.
**Consumed by**: inventory.
**Delivery**: persistent, publisher confirms on, manual acknowledgement after the consumer's transaction commits.

```json
{
  "messageId": "0f9c2b1e-5a44-4d1e-9f03-7a2c8e6b4d10",
  "occurredAt": "2026-10-30T14:21:08.412Z",
  "transactionId": "tx-48213",
  "lines": [
    { "sku": "SKU-0042", "qty": 3 },
    { "sku": "SKU-1180", "qty": 1 }
  ]
}
```

| Field           | Type         | Meaning                                                                                                 |
| --------------- | ------------ | ------------------------------------------------------------------------------------------------------- |
| `messageId`     | UUID         | the outbox row's id. The consumer deduplicates on this and nothing else.                                |
| `occurredAt`    | ISO 8601 UTC | when the sale committed, not when the message was sent. The two differ when the broker was unreachable. |
| `transactionId` | string       | which basket. Carried for tracing only; inventory does not key on it.                                   |
| `lines`         | array        | what was sold. Quantities are positive; the consumer decides they mean a reduction.                     |

**Guarantees the publisher makes**: the message describes a sale that is already committed in checkout's database. It will be sent at least once, eventually, even across a crash or a broker outage.

**Guarantees the consumer must make**: applying it twice has the same effect as applying it once, and the message is not acknowledged until the stock change is committed.

### `items.scanned`

**Published by**: checkout, from its background scan writer, in batches of up to 200.
**Consumed by**: analytics.
**Delivery**: transient, no confirms, dropped on a full queue.

```json
{
  "messageId": "6b1d7f90-2c33-4a88-b0e1-11d9c5a73e2f",
  "skus": ["SKU-0042", "SKU-0042", "SKU-1180", "SKU-0007"]
}
```

| Field       | Type            | Meaning                                                                                                                                                               |
| ----------- | --------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `messageId` | UUID            | present for tracing and for a consumer that wants to deduplicate. Analytics does not, because a duplicated scan skews a ranking by one and costs nothing to tolerate. |
| `skus`      | array of string | one entry per scanned unit, repeats intended. Names are not carried; analytics holds its own catalog.                                                                 |

**What this path deliberately does not guarantee**: that a scan arrives at all.
Principle III permits analytics to lag, FR-019 requires the bound on undelivered messages to be stated, and the scan writer sheds rather than blocks when its queue is full.
A shopper is never made to wait for a reporting message.

---

## What a reader should check this contract against

- No service holds another service's network address. The only addresses in configuration are the broker's and each service's own database.
- No message is a question. Neither carries a reply queue or a correlation id, because nothing waits for an answer.
- Both messages are published by the service that owns the facts they describe, and consumed by services that decide for themselves what to do about them.

# Phase 1 Data Model: Microservices Self-Checkout

Week 5 had one schema with seven tables that three services shared.
This week that schema splits three ways, and two tables are added to make the saga survivable.

## Where every table went

| Table                 | Week 5 (one store)             | Checkout store | Inventory store | Analytics store |
|-----------------------|--------------------------------|----------------|-----------------|-----------------|
| `catalog`             | shared, read by all three      | **replica**    | **owner**       | **replica**     |
| `stock`               | written by checkout            |                | **owner**       |                 |
| `transactions`        | checkout only                  | **owner**      |                 |                 |
| `transaction_items`   | checkout only                  | **owner**      |                 |                 |
| `scan_log`            | checkout wrote, analytics read |                |                 | **owner**       |
| `popularity_state`    | analytics only                 |                |                 | **owner**       |
| `popularity_snapshot` | analytics only                 |                |                 | **owner**       |
| `payment_saga`        | did not exist                  | **new**        |                 |                 |
| `applied_requests`    | did not exist                  |                | **new**         |                 |

The two rows that were genuinely shared last week are the two that forced this week's work.
`stock` was written by checkout and read by inventory; it now belongs to inventory alone, and checkout has to ask.
`scan_log` was written by checkout and read by analytics; it now belongs to analytics alone, and checkout has to push.

## Checkout store (port 5437)

**`catalog`** - replica, read-only after seeding. Same three columns as week 5: `sku` (primary key), `name`, `price`. Present so a scan can return a name and price, and a receipt line can record what was charged, without a network call. Safe to duplicate because catalogue rows never change while the system runs.

**`transactions`** - unchanged from week 5 except for one added column.

| Column           | Notes                                                              |
|------------------|--------------------------------------------------------------------|
| `transaction_id` | primary key, `tx-N` from a sequence                                |
| `station_id`     | required                                                           |
| `status`         | `OPEN`, `COMPLETED` or `CANCELLED`. **No fourth value**, see below |
| `item_count`     | running count                                                      |
| `running_total`  | running total                                                      |
| `started_at`     | set on creation                                                    |
| `completed_at`   | set when the saga finishes                                         |
| `paying_since`   | **new**, nullable, never serialised                                |

`paying_since` is the whole reason the contract survives this week.
The transaction status is fixed by `spec/self-checkout-openapi.yaml:284` to three values, so a basket mid-payment cannot be marked `PAYING` without breaking Principle I.
Instead it stays `OPEN` on the wire and carries a timestamp that nothing outside checkout can see.

It does two jobs:

1. **Mutual exclusion.** Claiming a basket for payment is one conditional update, `WHERE status = 'OPEN' AND paying_since IS NULL`, so two concurrent payments cannot both proceed.
2. **Freezing the basket.** The scan guard gains the same condition, so no item can land in a basket after its receipt lines have been computed.

**`transaction_items`** - unchanged. One row per scanned unit, carrying the name and price at the time of sale so a receipt reflects what was actually charged.

**`payment_saga`** - new. One row per payment attempt, recording how far it got.

| Column           | Notes                                                                                                                          |
|------------------|--------------------------------------------------------------------------------------------------------------------------------|
| `saga_id`        | primary key, generated per attempt. Sent to inventory as its `idempotencyKey`, because inventory has no concept of a saga      |
| `transaction_id` | unique, so one basket cannot have two live sagas                                                                               |
| `state`          | `PENDING`, `APPLIED`, `DONE`, `COMPENSATED`. Four, deliberately: see below                                                     |
| `lines`          | the SKUs and quantities this payment must reduce, frozen at claim time                                                         |
| `attempts`       | how many times the stock call has been tried. Telemetry only: nothing branches on it, and the retry budget lives in the client |
| `created_at`     | used by recovery at startup to find rows left part-way                                                                         |
| `updated_at`     |                                                                                                                                |

Without this table a crash between "stock reduced" and "basket marked paid" is unrecoverable, because nothing afterwards can distinguish a payment that never started from one that got half way.

State transitions:

```
claim -> PENDING -> APPLIED -> DONE          (receipt returned)
|     |
            v          v
          COMPENSATED                        (payment refused, basket reopened)
```

`PENDING` means the stock call has been sent and its outcome is unknown.
`APPLIED` means inventory confirmed it.
`DONE` and `COMPENSATED` are the only two end states, and every saga must reach one of them.

**Why four states and not six.**
An earlier draft also carried `COMPENSATING` and `FAILED`.
`FAILED` described the same outcome as `COMPENSATED` from a different angle, since a refused payment is exactly one whose stock was put back.
`COMPENSATING` was meant to tell recovery that the decision to give up had already been taken, but a crash during compensation leaves the shopper with no response either way, so recovery may drive that saga to either end state and the books stay correct.
Both states bought tidiness rather than recoverable information, and a state machine with unreachable distinctions is harder to reason about than one without them.

## Inventory store (port 5438)

**`catalog`** - owner. The authoritative copy; the other two are seeded from the same data.

**`stock`** - owner. `sku` (primary key, references `catalog`), `qty` with a `CHECK (qty >= 0)` constraint and an index on `qty` for the low-stock query. Unchanged from week 5 except that checkout can no longer reach it.

**`applied_requests`** - new. `idempotency_key` as primary key, plus `applied_at`.

Named for what inventory sees rather than for what checkout is doing.
Checkout sends its saga id as the key, but nothing here depends on that: inventory treats the value as opaque and only ever compares it to keys it has already recorded.
That is deliberate, and it is why the table is not called `applied_saga`. A table in inventory's schema named after checkout's orchestration pattern would be exactly the cross-service knowledge this week exists to remove.

This is what makes a repeated instruction harmless.
Reducing stock inserts the key and applies the decrement in the same local transaction; a second delivery conflicts on the primary key and the decrement is skipped.
Retries are required by FR-013 and recovery can re-send a step it is unsure about, so duplicate delivery is expected rather than exceptional.

## Analytics store (port 5439)

**`catalog`** - replica, for names in the ranking.

**`scan_log`** - owner, unchanged in shape: `seq` from a sequence, `sku`, `scanned_at`. Rows arrive over HTTP in batches from checkout instead of being inserted by it directly.

**`popularity_state`** - owner, unchanged. Single row holding the window size, slide interval, and which stretch of the stream was last counted.

**`popularity_snapshot`** - owner, unchanged. The current ranking, replaced wholesale each rebuild.

## What the invariant check becomes

Week 5 checked correctness with one query joining `stock` to `transaction_items`.
Those tables are now in different databases, so a single query cannot see both.

The check becomes: read units sold per SKU from the checkout store, read final quantities from the inventory store, and compare them outside the database.
`scripts/check_invariant.sh` does this and reports the same four counts as previous weeks, so the result stays comparable.

This is a real loss worth naming in the write-up: the correctness property that was previously enforceable by the database is now only checkable by a program that reads both stores.

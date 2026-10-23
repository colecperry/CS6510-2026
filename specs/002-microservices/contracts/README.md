# Interface Contracts

Two kinds of interface now exist, and keeping them apart is what stops this week breaking the comparison.

**The public contract** is the seven endpoints from `spec/self-checkout-openapi.yaml`.
It is fixed and identical to every previous week.

**Internal endpoints** are new this week and exist only so services can coordinate.
They are not part of the graded contract, are not routed by the gateway, and are not reachable from outside.

## Public routing table (unchanged from week 5)

The gateway matches on the longest path prefix and forwards.
It holds no business rules and opens no database connection.

| Path prefix     | Owner     | Port |
|-----------------|-----------|------|
| `/transactions` | checkout  | 8081 |
| `/items`        | inventory | 8082 |
| `/inventory`    | inventory | 8082 |
| `/analytics`    | analytics | 8083 |

The seven endpoints:

| Method | Path                          | Owner     |
|--------|-------------------------------|-----------|
| POST   | `/transactions`               | checkout  |
| POST   | `/transactions/{id}/items`    | checkout  |
| POST   | `/transactions/{id}/complete` | checkout  |
| GET    | `/transactions/{id}`          | checkout  |
| GET    | `/items`                      | inventory |
| GET    | `/inventory/low-stock`        | inventory |
| GET    | `/analytics/popular-items`    | analytics |

`/internal` is deliberately absent from the routing table, so no internal endpoint can be reached through the gateway.

## Internal: inventory

### `POST /internal/stock/reduce`

Called by checkout during a payment. Applies at most once per `idempotencyKey`.

The key is a string inventory treats as opaque: it only ever compares it to keys it has already seen. Checkout happens to send its saga id, but inventory has no concept of a saga and does not need one.

Request:

```json
{
  "idempotencyKey": "saga-8f3a...",
  "lines": [{ "sku": "SKU-000001", "qty": 3 }]
}
```

Responses:

- `200` `{"applied": true, "alreadyApplied": false}` - the decrement was applied now.
- `200` `{"applied": true, "alreadyApplied": true}` - this saga was already applied; nothing changed. Checkout treats this exactly like the first case, which is what makes retrying safe.
- `409` - a line could not be satisfied. Stock floors at zero rather than going negative, as in every previous week, so in practice this is reserved for a malformed request.
- `503` - inventory is up but not ready to serve. Checkout retries within its budget.

### `POST /internal/stock/restore`

Compensation. Puts back exactly what the named saga took, and is itself idempotent: restoring a saga that was never applied, or already restored, succeeds and changes nothing.

Request: `{"idempotencyKey": "saga-8f3a..."}`
Response: `200` `{"restored": true}`

### `GET /internal/stock/applied/{idempotencyKey}`

Asks whether a given key was ever applied. Used only by recovery at startup, when checkout comes back holding a payment it does not know the outcome of.

Response: `200` `{"applied": true | false}`

## Internal: analytics

### `POST /internal/scans`

Called by checkout's background writer with a batch of scans. Fire-and-forget: checkout does not block a shopper on it and does not retry indefinitely.

Request:

```json
{ "skus": ["SKU-000001", "SKU-000007", "SKU-000001"] }
```

Response: `202` `{"accepted": 3}`

Scans are counted, not keyed, so a duplicated batch would slightly overcount the ranking and nothing else.
This is accepted: the ranking is the one thing the constitution allows to be approximate, and adding idempotency keys here would cost more than the error it prevents.

## Error shape

Unchanged from previous weeks, and produced by the same shared mapping in `common/error_handlers.py` so three services cannot disagree about what a conflict looks like:

```json
{ "code": "TRANSACTION_NOT_OPEN", "message": "Transaction tx-4821 is already completed." }
```

Internal endpoints use the same shape, so a failure surfaced from inventory reads the same as one raised locally.

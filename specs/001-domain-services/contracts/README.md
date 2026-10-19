# Contracts: Service-Based Self-Checkout

**Date**: 2026-10-13
**Feature**: [spec.md](../spec.md)

Two contracts matter this week.

The **external** one is unchanged and is not restated here: it is
[`spec/self-checkout-openapi.yaml`](../../../spec/self-checkout-openapi.yaml), the same file the
previous three weeks implement.
Seven endpoints, identical shapes, identical error codes. FR-001.

The **internal** one is new: which service owns which endpoint, and how the entry point decides.

---

## Routing

The entry point matches on the URL path and forwards. It parses nothing else, and holds no rules.

| Method | Path                          | Owned by  |
|--------|-------------------------------|-----------|
| POST   | `/transactions`               | selling   |
| POST   | `/transactions/{id}/items`    | selling   |
| POST   | `/transactions/{id}/complete` | selling   |
| GET    | `/transactions/{id}`          | selling   |
| GET    | `/items`                      | stock     |
| GET    | `/inventory/low-stock`        | stock     |
| GET    | `/analytics/popular-items`    | reporting |

Longest matching prefix wins, so `/items` and `/inventory/...` route to stock while `/transactions/...` routes to selling, with no overlap between them.

**Forwarding rules**:

- Method, path, query string, headers and body pass through unchanged.
- The owning service's response status, headers and body pass back unchanged.
- The entry point adds nothing to the response and interprets nothing in it. An error from a service reaches the caller exactly as that service wrote it.
- A path matching no service is a 404 from the entry point itself. The load client never requests one.

**What the entry point must not do**, from FR-005a:

- No database connection.
- No business rules. It never decides whether a basket is open, what an item costs, or whether a ranking is due.
- No response rewriting, caching, or retrying.

---

## Service boundaries

### Selling

Owns the shopper journey, and owns every write to baskets and stock.

Also writes scan records, because reporting cannot be told about a scan directly.
This is the one piece of work it does on another service's behalf, and it is done by writing to shared storage rather than by calling anyone.

### Stock

Owns reads about what is on the shelves: the product list, and which items are running low.

Writes nothing. Reads stock levels that selling writes.

### Reporting

Owns the most-scanned ranking, and the background work that rebuilds it.

Reads the scan records selling writes, and discards those too old to count.

---

## The rule that must hold

**No service sends a request to another service.** FR-005.

Verifiable by inspection: no service's code should contain the address of another service, or an HTTP client used to reach one.
Only the entry point knows where the services are.

This is worth checking mechanically rather than by eye, since it is the single rule the assignment is most explicit about.

---

## Independent operability

From FR-004 and SC-005: each service answers its own requests with the others stopped.

| Started alone | Should answer                    | Should fail     |
|---------------|----------------------------------|-----------------|
| selling       | the four transaction paths       | the other three |
| stock         | `/items`, `/inventory/low-stock` | the other five  |
| reporting     | `/analytics/popular-items`       | the other six   |

Testable by starting one service and calling it directly, without the entry point.
If a service only works when another is running, the boundary is in the wrong place.

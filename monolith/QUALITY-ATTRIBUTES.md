# Quality Attributes and Architectural Characteristics

Week 1: monolithic implementation (Python, FastAPI, PostgreSQL, raw SQL via asyncpg).
Performance claims come from the two runs in `reports/`.

## Part 1: Characteristics the system must have

**Data integrity.** A store's inventory count isn't allowed to be approximately right.
- For every SKU, `starting_stock - ending_stock` must equal units sold in completed transactions.
- Stock must never go negative.
- A transaction that fails partway must leave no partial changes behind.

**Performance.** There's a real person at the till waiting for the beep.
- Scan under 100ms at p95.
- Start a transaction under 1s.
- Complete under 2s at p95.

**Concurrency.** Stations all compete for the same inventory rows.
- Support 10 stations minimum, survive 100.
- Two stations completing baskets with the same SKU must not lose either decrement.

**Availability.** If checkout is down, the store can't take money.
- 99.9% during store hours, roughly a minute a day.
- A server restart must not lose committed transactions.

**Scalability.**
- Absorb 10x the stations (holiday rush) without a redesign.
- Handle the catalog growing past 2,000 SKUs.

**Reliability.**
- The database is the source of truth, so in-memory state must be rebuildable on startup.
- A crash may lose an open basket, but must never corrupt inventory.

**Observability.**
- Latency spikes must be attributable to a specific operation, not just "checkout is slow."
- Percentiles over averages, since the average hides the customers having the worst time.

**Maintainability.** The same contract gets rebuilt in a new style every week, so anything that tangles business rules into delivery mechanics is a cost I pay again.
- The stock decrement and window logic should be readable without knowing FastAPI.

**Testability.**
- Resettable to an identical starting state between runs, or no two runs are comparable.

**Security.** The spec stubs out payment, so no card data is in scope.
In a real build this ranks far higher: a compromised checkout system is a compromised cash register.

**Consistency.** Not all data here needs the same guarantee.
- Stock counts need strong consistency. They're money.
- Popular-items analytics can lag. Nobody is harmed by stale trending data.

## Part 2: The three I prioritized, and what they cost

### 1. Data integrity

Everything else was negotiable, this wasn't.

Every check that guards a state change lives inside the statement that performs it, so there's no window for another request to slip in: `UPDATE ... WHERE transaction_id = $1 AND status = 'OPEN'` rather than read-then-decide-then-write.
The decrement is one atomic statement (`UPDATE stock SET qty = GREATEST(qty - $2, 0) WHERE sku = $1`), so Postgres serializes concurrent completions on the row lock rather than letting them race.
Completion runs in one database transaction, so the status flip and every decrement either all land or none do.

**Trade-off: this is the direct cause of the worst latency in the system.**
When 100 stations all want SKU-000001, they queue.
COMPLETE_TRANSACTION p99 went from 30ms to 4,486ms, a 149x degradation.
That's not a bug, it's the bill for correctness arriving, and I'd pay it again.
The alternative is a checkout system that's fast and wrong.

### 2. Responsiveness on the scan path

Scanning is where the customer feels the system, and it runs ~10x more often than the other operations (147,182 scans vs 14,085 transactions under stress).

The catalog is loaded into memory at startup, so a SKU lookup is a dictionary read, not a database round trip.
Running totals are updated incrementally instead of recomputed per scan.
The popularity ranking recalculates only every 500th scan, so 499 of every 500 scans pay one cheap conditional UPDATE that matches nothing.

It worked: SCAN_ITEM p95 only moved 9.68ms to 18.39ms between runs, while completion fell apart.

**Trade-offs.**
Caching the catalog is only safe because the contract has no endpoint that changes it.
If prices could change mid-day, every running server would keep charging the old one until restarted.
Deferring the popularity recompute means analytics can be up to 499 scans stale, which is fine for "what's trending" and useless for real-time restocking.

### 3. Concurrency and scalability

Decrements are applied in fixed SKU order so overlapping baskets take row locks in the same sequence and can't deadlock.
The connection pool is sized above the station count, with Postgres's `max_connections` raised to match.

**Trade-off: a single process against a single database is both why it's simple and why it's fragile.**
One process is easy to reason about and still pushed 1,222 items/sec.
It's also a single point of failure, which sits badly next to that 99.9% availability target.
One database gives strong consistency for free and is simultaneously the bottleneck everything queues behind.
That tension is presumably what the later weeks exist to attack.

### One trade-off the spec chose for me

Stock decrements at completion, not at scan, so inventory is overstated while baskets are open and overselling is possible.
We hit this: SKU-000001 had 17,884 units demanded against 10,000 in stock.
It floored at 0 rather than going negative, which is correct given the contract has no insufficient-stock path.
Every other SKU matched the invariant exactly.
A real store would reserve stock at scan time, or be allowed to say no.

## What the measurements showed

| | Default (10 stations, 60s) | Stress (100 stations, 120s) | Change |
|---|---|---|---|
| Transactions | 5,905 (98.3/sec) | 14,085 (117.0/sec) | +19% |
| Items scanned | 62,027 (1,032.6/sec) | 147,182 (1,222.2/sec) | +18% |
| Error rate | 0.00% | 0.00% | - |
| START_TRANSACTION p95 | 3.79ms | 6.59ms | 1.7x |
| SCAN_ITEM p95 | 9.68ms | 18.39ms | 1.9x |
| COMPLETE_TRANSACTION p95 | 21.48ms | 2,722.91ms | 127x |
| COMPLETE_TRANSACTION p99 | 30.18ms | 4,486.00ms | 149x |

**Throughput barely moved.** Ten times the stations bought 19% more transactions per second.
The system was already saturated well below 100 stations, so the extra load became queueing rather than work.

**The pain is concentrated in one operation.** Start and scan degraded under 2x; completion degraded over 100x.
That matches the design: completion is the only operation running several statements in one transaction while holding locks on SKUs every other station also wants.
Zipf sampling makes it worse, piling contention onto a handful of popular SKUs instead of spreading it across 2,000.

**It got slow, not wrong.** 0.00% errors in both runs, zero unexplained invariant violations across 147,182 scans.
Of the available failure modes for a system that tracks money, degrading into latency is the one to pick.

If I were raising the ceiling, the completion path is the only thing worth touching: collapsing its statements into one round trip would shorten how long each transaction holds its locks, which is what the queueing is made of.

## Appendix: two defects the load client found

The Java client attaches `Upgrade: h2c` to every request.
Under uvicorn that silently dropped the request body and failed every request with a 422; a raw TCP capture confirmed the client's request was well-formed, so the fault was server-side.
Hypercorn fixed that but ignores the offer only on requests with a body, so the client's one bodyless `GET /items` at startup triggered a real upgrade, after which everything went over HTTP/2 and destabilized under sustained load.
Refusing the upgrade outright took stress mode from unusable to 0.00% errors.

Worth noting that both defects lived in a protocol path neither manual `curl` testing nor in-process test clients ever touched.

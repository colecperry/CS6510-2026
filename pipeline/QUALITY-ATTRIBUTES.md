# Quality Attributes and Architectural Characteristics

Week 4: the windowed analytics rebuilt as pipes and filters, on top of week 2's layered implementation.
Performance claims come from the two runs in `reports/`, compared against `../monolith/reports/` and `../layered/reports/`.

Week 2 asked what the layering bought and cost, and the answer was: better structure, no measurable performance change.
This week is the opposite.
The structure changed in one small place, and the performance changed a lot.

## Part 1: What the pipeline changed

### Responsiveness on the scan path went up sharply

This is the characteristic week 1 named second in priority, and the one this week actually moved:

> Scanning is where the customer feels the system, and it runs ~10x more often than the other operations.

| SCAN_ITEM p95 | monolith | layered | pipeline    |
|---------------|----------|---------|-------------|
| Default       | 9.68ms   | 9.49ms  | **7.10ms**  |
| Stress        | 18.39ms  | 19.37ms | **10.88ms** |

A 25% improvement at default load and 44% under stress, with throughput up 24%.

The cause is not clever code.
It is that two database round trips left the request path.
A scan used to wait for its row to be written and for a shared counter to be checked, neither of which affects what the customer is told.
Now it waits for neither.

The gain being larger under stress is the interesting part.
At 100 stations, the "is a rebuild due?" check was 100 requests a second contending on one row.
It now runs once per batch, so the contention mostly disappeared.

### Throughput went up without touching the bottleneck

124.5 transactions per second under stress against 115.8, and 123.6 against 99.7 at default load.

Worth being precise about why: the system did not get more capable, it stopped wasting capacity.
The database work per scan fell from two round trips to roughly one five-hundredth of one, and that freed capacity went into serving more requests.

### Two correctness improvements came for free

Neither was the goal, and both are consequences of having exactly one of each filter:

**A write race disappeared.**
Previously 100 concurrent requests inserted into `scan_log` independently.
When a window was counted, some rows below its end had not committed yet, so they counted as zero and were then pruned unseen - an undercount that no test would catch.
A single ingest filter serialises those writes, so everything inside a window is committed before it is counted.

**Publishing became single-writer by construction.**
`popularity_snapshot.rank` is a primary key, so two concurrent publishes would collide.
Previously that was prevented only by the mutual exclusion inside the due-check.
Now there is one ranker, so there is only ever one publisher, and the protection is structural rather than incidental.

### Consistency moved, deliberately

Week 1 drew a line that still holds:

> Stock counts need strong consistency. They're money.
> Popular-items analytics can lag. Nobody is harmed by stale trending data.

This week spends exactly the slack that line allows.
A scan is acknowledged before it has been recorded for analytics, so the ranking is now behind by however long the pipeline takes to catch up.

The amount is small, and smaller than it sounds.
The ranking was already up to 499 scans stale by design, because it only rebuilds every 500 scans.
Adding a few milliseconds of queue time to something already seconds old costs nothing real.

The line itself did not move.
Stock decrements are still synchronous, still inside one database transaction, still acknowledged only once committed.

## Part 2: What it cost

### Completion got slower at default load, and I did not predict it

| COMPLETE_TRANSACTION p95 | layered    | pipeline    |
|--------------------------|------------|-------------|
| Default                  | 20.95ms    | **31.95ms** |
| Stress                   | 2,712.42ms | 2,726.74ms  |

A 50% regression at default load, and no change under stress.

The likely cause is the throughput gain itself.
The system now completes 24% more transactions per second, and completion holds row locks while decrementing stock, so more completions per second means more of them queueing on the same popular items.
Under stress this does not show because the path was already saturated.

**This is a hypothesis, not a measurement.**
Confirming it would mean rate-limiting the pipeline version to the old throughput and re-measuring, which has not been done.
It is recorded here because a 50% regression that is explained away without evidence is worse than one that is simply reported.

### The bottleneck did not move

Completion is still where the system falls over: 2.7 seconds at p95 under stress, against 11ms for a scan.

That was never going to change.
The cost is the data integrity guarantee - a database transaction held open while stock comes down row by row - and reorganising analytics does not touch it.
Week 1's closing note still stands as the thing worth doing: collapsing completion into fewer round trips would shorten lock hold time, which is what the queueing is made of.

Worth stating plainly, because it would be easy to present "we built a pipeline" and "the system is faster" as more connected than they are.
The pipeline made the frequent cheap operation faster. The rare expensive one is untouched.

### New failure modes that did not exist before

Making work asynchronous means it can now fail in ways a synchronous call could not:

**Scans can be dropped.** The incoming queue is bounded at 50,000, and a full queue discards rather than blocking. Neither run dropped any, but the possibility is real and is counted and logged.

**Scans can be lost on shutdown.** Anything still queued when the process stops has not been written. Shutdown drains the pipeline first, with a five second limit, so this requires both a backlog and a slow database.

**A filter can die.** A crash in a background task is silent in a way an exception in a request handler is not - no 500, no stack trace reaching anyone. Each filter is wrapped so an error is logged and the filter restarts.

None of these can lose money.
All of them are on the analytics path, which is the one week 1 designated as allowed to lag.
That is not a coincidence - it is why this was the piece chosen to pipeline.

### Complexity, honestly

`pipeline.py` is about 160 lines that did not exist, replacing 25 lines of straight-line code.

Following a scan now means understanding three background tasks and three queues rather than reading one function top to bottom.
Debugging is harder: a problem shows up as "the ranking is stale" rather than as an exception in a request.

For this feature the trade is clearly worth it - a 44% latency improvement on the most frequent operation in the system.
Applied to checkout itself it would not be, which is what the assignment means by the application not generally suiting a pipeline.

## Part 3: Characteristics, revisited

| Characteristic          | Week 2 (layered)                 | Week 4 (pipeline)                                 |
|-------------------------|----------------------------------|---------------------------------------------------|
| Performance, scan path  | 9.49ms p95                       | **7.10ms p95**, and 44% better under stress       |
| Performance, completion | 20.95ms p95                      | 31.95ms p95, unexplained                          |
| Throughput              | 99.7 tx/sec                      | **123.6 tx/sec**                                  |
| Data integrity          | held                             | held, and one write race removed                  |
| Consistency             | analytics could lag 499 scans    | analytics can lag 499 scans plus queue time       |
| Reliability             | a failure meant a failed request | a failure can be silent; filters restart on error |
| Testability             | 7 tests, no database             | 9 tests, no database                              |
| Maintainability         | rules separated from delivery    | one more concept to hold: filters and pipes       |
| Availability            | single process, single database  | unchanged                                         |

The availability row is unchanged for the same reason as week 2.
Filters are tasks inside one process, not separate services.
If the process dies, everything dies with it, and the single point of failure week 1 identified is exactly as present as it was.

## What the measurements showed

|                               | Default (10 stations, 60s) | Stress (100 stations, 120s) |
|-------------------------------|----------------------------|-----------------------------|
| Transactions                  | 7,426 (123.6/sec)          | 14,990 (124.5/sec)          |
| Items scanned                 | 78,167 (1,300.9/sec)       | 158,157 (1,313.7/sec)       |
| Error rate                    | 0.00%                      | 0.02% (3 client timeouts)   |
| SCAN_ITEM p95                 | 7.10ms                     | 10.88ms                     |
| SCAN_ITEM p99                 | 13.38ms                    | 36.36ms                     |
| COMPLETE p95                  | 31.95ms                    | 2,726.74ms                  |
| Scans dropped by the pipeline | 0                          | 0                           |

The shape has changed from the previous weeks.
Scanning used to degrade by about 2x between default and stress; it now degrades by 1.5x.
Throughput is nearly flat between the two runs, which says the system is saturated in both - but it is now saturated at a higher number.

Correctness held in both runs.
Default: all 2,000 SKUs exact.
Stress: 1,999 exact, with SKU-000001 selling out and flooring at zero, the same expected edge case the previous weeks documented.

Popular items ranked SKU-000001 first in all three weeks with the same Zipf-shaped tail.
That is the cross-week check the course README asks for, and it is the one that matters most here: the analytics behaviour survived being taken apart into filters and made asynchronous.

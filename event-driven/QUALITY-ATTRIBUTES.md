# Quality Attributes and Architectural Characteristics

Week 6: microservices, each owning its own database, with payment coordinated across two of them.
Performance claims come from the two runs in `reports/`, compared against the previous four weeks' reports.

Week 5 asked what splitting the code into services cost, and the answer was most of the throughput, all of it the gateway's fault.
This week asks a narrower question: what does giving each service its own database cost, on top of that.

## Part 1: What database-per-service changed

### The coupling that was left is now gone

Week 5's services were divided by business function but shared one database, so the brief's own phrasing was that coupling "occurs at the database access layer".
That was literally true: `common/` held every SQL statement, and checkout could read inventory's stock table by writing one import.

This week `common/` holds the wire contract, the domain errors, and the mechanics of opening a pool.
It holds no SQL at all.
Each query lives in the single service that owns its table, and each service is handed one database URL, its own.

The claim is checked rather than asserted.
`tests/test_boundaries.py` reads the source and fails if a service names a table it does not own, constructs another service's settings, or carries an HTTP client it has no reason to have.
Each of those checks was verified by planting a violation and watching it fail.

### Payment became a saga, and that is the week's real content

In every week up to now, paying was one database transaction: mark the basket paid, reduce the stock, both or neither.

Those two writes now live in different Postgres instances, and no transaction can span them.
What replaces it is a sequence with a record and a way back:

| Step | Where           | What is recorded                                        |
|------|-----------------|---------------------------------------------------------|
| 1    | checkout store  | basket claimed, and what this payment intends to reduce |
| 2    | inventory store | the stock change, keyed so a repeat is ignored          |
| 3    | checkout store  | basket paid, saga done                                  |

**Stock goes first deliberately.**
Both orders have a window where a crash leaves the two halves disagreeing, but they fail in opposite directions.
Stock-first leaves the shop believing it has less than it does, which cannot oversell.
Paid-first would hand a shopper a receipt for stock still on the shelf.

**Three separate mechanisms exist because they cover three different failures**, and it is worth being precise about which covers what:

- *A step returns an error*: the coordinator undoes what was applied and refuses the payment.
- *A reply goes missing*: the client retries, and inventory recognises the repeated key and changes nothing.
- *The process dies*: nothing is left running to compensate, so the saga row is read at the next startup and driven to an ending.

Only the third needed a durable record.
The brief asked for coordination, not for crash survival, so that part is beyond what was strictly required and is kept because "what if it dies here" is the obvious question about this design.

### Inventory must be exact, and still is

The constitution requires that for every SKU, stock sold equals stock reduced, and that inventory may not lag.
That was the hardest thing to keep, because the usual saga answer is eventual consistency.

It holds because stock is reduced before the receipt is returned, not after.
A shopper is never given a receipt for stock that has not come off the shelf.
Both runs confirm it: 2,000 SKUs exact, zero negative, zero unexplained, across two databases that cannot see each other.

## Part 2: What it cost

### Throughput fell 9% at default load and 26% under stress

|                 | service-based | microservices | change |
|-----------------|---------------|---------------|--------|
| Default, tx/sec | 45.1          | 41.0          | -9%    |
| Stress, tx/sec  | 37.6          | 27.8          | -26%   |

**I predicted the first number and missed the second.**
The plan said throughput would "stay near week 5's 45 tx/sec" because the gateway, not checkout, was the cap.
Default landed at 41.0, close enough.
Stress landed at 27.8, which is a quarter below week 5 and was not predicted.

The explanation is arithmetic rather than mystery, and it is about the shape of the test.
The load client runs a fixed number of stations, each doing one transaction at a time, so throughput is stations divided by the time one transaction takes.
At 100 stations, week 5 averaged 2.66 seconds per transaction and this week averages 3.60.
Anything that adds latency therefore removes throughput directly, and the saga added a network round trip plus a second commit to every payment.

What I got wrong was assuming a saturated gateway sets the throughput floor.
It sets a ceiling, not a floor: the system can be slower than the gateway allows, and under stress it now is.

### Completion got substantially slower

| COMPLETE p95 | service-based | microservices | change |
|--------------|---------------|---------------|--------|
| Default      | 30.58ms       | 47.05ms       | +54%   |
| Stress       | 486.44ms      | 734.76ms      | +51%   |

This is the cost landing exactly where the design said it would.
One local transaction became two commits in separate databases with an HTTP call between them, and the measurement is roughly what that costs.

### Scanning was almost untouched

| SCAN p95 | service-based | microservices | change |
|----------|---------------|---------------|--------|
| Default  | 28.30ms       | 29.96ms       | +6%    |
| Stress   | 490.93ms      | 749.83ms      | +53%   |

At default load the scan path is essentially unchanged, which was the goal.
Scans still go to an in-memory queue and the request ends there; only the destination changed from a shared table to another service's endpoint.

Under stress it rose with everything else, which reflects general queueing rather than new work on the scan path.

### The gateway is still the ceiling, and now it has company

| CPU during the run | gateway   | checkout | inventory | analytics |
|--------------------|-----------|----------|-----------|-----------|
| Default            | **87.3%** | 76.9%    | 7.3%      | 25.6%     |
| Stress             | **86.3%** | 54.4%    | 5.0%      | 14.7%     |

Week 5 measured the gateway at 87% of a core with checkout at 53%, and proved the gateway was the bottleneck by bypassing it.
The gateway is unchanged this week and sits at the same 87%.

Checkout is the number that moved: 52.9% last week, 76.9% this week at the same load.
That is the saga showing up as work rather than as a diagram. Per transaction, checkout now serialises a request, makes an outbound call, waits, and commits twice.

Inventory and analytics remain nearly idle, which says the split was lopsided: one service does almost everything and two answer occasionally.

### Correctness is no longer enforceable by the database

Every previous week checked the stock invariant with one SQL query, because units sold and units remaining sat in the same database and a join could see both.

They are now in separate instances with no connection between them.
The check reads each store, pulls both sides into memory, and compares them in Python.

The invariant did not get weaker, but the thing guaranteeing it moved out of the storage engine and into a script we wrote and have to maintain.
That is a real loss, and the kind that is easy not to notice: a program can have a bug, where a `JOIN` cannot.

### New failure modes

Splitting the stores created failures that could not previously exist:

- **A payment can be left part way** if checkout dies between the two writes. Handled by recovery at startup, which cannot run until the process comes back.
- **A payment can be refused because another service is down.** The contract defines no status code for "a service we depend on is unavailable", so this returns 409, which is semantically wrong but is the least wrong of the three the contract allows.
- **A basket can be locked out.** If compensation itself cannot reach inventory, `paying_since` is never cleared and the shopper cannot scan or retry until checkout restarts. This contradicts FR-013, which requires the basket to be left in a state the shopper can retry from. It is deliberate: releasing a basket whose stock may already be down invites a second payment and a double deduction, and the constitution makes stock exactness the thing not to trade. **Recorded here rather than fixed.**
- **Scans can be lost outright.** If analytics is down, batches are counted and abandoned rather than retried, because a writer stuck retrying backs the queue up and starts dropping scans anyway. During testing, 120 batches were lost this way and are not recoverable.

None of these can lose money. All of them are on paths the constitution allows to degrade, except the basket lock, which is recorded above as a known violation.

### Complexity, honestly

Five new source files, about 370 lines, of which roughly 130 are what the brief strictly asked for.
The rest is idempotency, forced by the decision to retry, and the durable saga record, forced by wanting the invariant to survive a crash.

Following a payment now means reading a coordinator, a client, a saga repository, a stock mover on the other side, and a recovery routine. In week 1 it was one function.

For this assignment the trade is the point. For a real shop of this size it would not be: one database and one transaction solved the same problem in a tenth of the code, with better numbers.

## Part 3: Characteristics, revisited

| Characteristic | Week 5 (service-based)                                            | Week 6 (microservices)                                          |
|----------------|-------------------------------------------------------------------|-----------------------------------------------------------------|
| Throughput     | 45.1 tx/sec default, 37.6 stress                                  | **41.0 and 27.8**                                               |
| Scan latency   | 28.30ms p95                                                       | 29.96ms p95, essentially unchanged                              |
| Completion     | 30.58ms p95                                                       | **47.05ms p95**, the saga's cost                                |
| Data integrity | held, enforced by one transaction                                 | held, enforced by a sequence plus compensation and recovery     |
| Verifiability  | one SQL query could prove the invariant                           | **a program must read two databases to prove it**               |
| Coupling       | shared database; any service could read any table                 | each store reachable only by its owner, checked by a test       |
| Independence   | services shared a database, so none could be deployed truly alone | each has its own store and starts with the others stopped       |
| Availability   | one database was a single point of failure                        | three, so one failing takes down less, but payment needs two up |
| Reliability    | a failed step could not leave a half state                        | it can, and is repaired at the next startup                     |
| Error rate     | 0.00% both runs                                                   | 0.00% both runs                                                 |

**The availability row is the one genuine architectural win**, and it is smaller than it looks.
Losing the analytics database now costs the ranking and nothing else; shoppers keep shopping.
Losing inventory's database stops payment but not scanning.
In week 5, losing the one database stopped everything.

**Independence is the other.**
Each service was started alone, with the other three stopped, and answered the requests it owns. That was not true last week in any meaningful sense, because all three needed the same database process running.

## What the measurements showed

|                      | Default (10 stations, 60s) | Stress (100 stations, 120s) |
|----------------------|----------------------------|-----------------------------|
| Transactions         | 2,471 (41.0/sec)           | 3,418 (27.8/sec)            |
| Items scanned        | 25,798 (428.2/sec)         | 35,610 (290.0/sec)          |
| Error rate           | 0.00%                      | 0.00%                       |
| START p95            | 29.30ms                    | 936.01ms                    |
| SCAN p95             | 29.96ms                    | 749.83ms                    |
| COMPLETE p95         | 47.05ms                    | 734.76ms                    |
| SKUs exact           | 2,000 of 2,000             | 2,000 of 2,000              |
| Payments compensated | 0                          | 0                           |
| Payments unfinished  | 0                          | 0                           |

Popular items ranked SKU-000001 first in both runs with the same Zipf-shaped tail as every previous week.
That is the cross-week check the course README asks for, and it confirms reporting survived being moved behind a network boundary.

## What was verified, and what was assumed

**Verified.** Every figure above comes from the two JSON reports or from a command whose output is quoted. The boundary tests were proven non-vacuous by planting four violations. The invariant check was proven non-vacuous by planting a 7-unit discrepancy. Idempotency was tested against the real database, including five concurrent copies of one request. Recovery was tested by simulating a crash between the two writes and restarting the service, which completed the sale without double-reducing stock.

**Assumed.** That Postgres is not a constraint. Its CPU could not be read, because Docker Desktop runs it in a VM whose process table is not visible from the host. Three database processes now, so less of the machine is accounted for than in week 5. The argument against Postgres being the limit is that the same hardware served 124 tx/sec in week 4 and is being asked for a fifth of that here, but it is an argument rather than a measurement.

**Not exercised by the load runs.** Zero payments were compensated and none were left unfinished, so none of the failure machinery ran during the measured runs. It is covered by 45 tests and by the manual drills in the README, not by these numbers.

**A regression I did not predict.** The stress throughput drop of 26%. The plan predicted throughput would hold near week 5's. It is recorded in Part 2 with the reasoning that was wrong, rather than being presented as expected.

## A note on choreography

The assignment permitted orchestration or choreography, and this is orchestration: checkout drives the sequence and issues the compensation.

Choreography would have meant each service publishing events and reacting to others', with no central coordinator.
It was rejected for two reasons.
The contract returns a complete receipt from the payment call, so a caller must be given a final answer before that call returns, and an event-driven payment would have to block waiting for a round trip anyway.
And it needs a message broker, which is a new moving part and a new failure mode for a system with three services.

What it would have bought is looser coupling: checkout would not need to know that inventory exists. What it would have cost is that no single file would describe what paying does, which matters more at this size than the coupling does.

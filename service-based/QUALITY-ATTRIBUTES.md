# Quality Attributes and Architectural Characteristics

Week 5: three domain services over one shared database, with no service-to-service calls.
Performance claims come from the two runs in `reports/`, compared against the three previous weeks.

Week 2 restructured the code and performance did not move.
Week 4 restructured one feature and performance improved sharply.
This week distributed the system across processes, and performance fell by two thirds.

That is the headline, and it is not a failure.
It is the first week where the architecture had a real price, and seeing the size of that price is the point of running the same client every week.

## Part 1: What distribution bought

### Independent deployability, demonstrated rather than claimed

Each service starts, stops and answers on its own.
That was verified one service at a time before the gateway existed: checkout alone at T022, inventory alone at T027, analytics alone at T031.
Each answered the requests it owns with the other two stopped.

This is the characteristic the previous weeks could not have at all.
A layered monolith has boundaries you can read; it does not have boundaries you can restart independently.

### Fault isolation, partially

Stopping one service leaves the others answering.
A request for a stopped service fails with a 500 from the gateway, and requests for the running ones are unaffected.

Partially, because the gateway is a single point of failure for everything, and the database is a single point of failure for everything.
Three services sharing one database are not three independent systems.
That is the defining limitation of service-based architecture as against microservices, and it is a deliberate part of the style rather than an oversight here.

### Boundaries that cannot quietly rot

In week 2 the layer rules were conventions. Nothing stopped a router importing a repository except care.

This week the rules are enforced two ways.
The three services have no HTTP client installed, so they physically cannot call each other.
The gateway has no database library, so it physically cannot query anything.
And `tests/test_boundaries.py` parses every file and fails if a service imports another service, mentions another service's address, or carries an HTTP client.

That test was written before the services it polices, and was verified against a deliberately planted violation.
It is the only way to check this rule, because a system that broke it would behave identically to one that did not.

### The coupling became visible

The brief asks you to think about how shared database access is distributed.
Doing so forced the ownership question into the open: exactly two tables are shared, `stock` and `scan_log`, each written by one service and read by another.

In the previous weeks that coupling existed too, but it was invisible because everything was in one process.
Naming it is worth something.

## Part 2: What it cost

### Throughput fell by two thirds

| | pipeline | service-based |
|---|---|---|
| Default, transactions/sec | 123.6 | **45.1** |
| Stress, transactions/sec | 124.5 | **37.6** |

Every request now crosses an extra process.
At default load, start and scan each gained about 22ms while completion barely moved, which is the signature of a fixed per-request cost rather than of the service split itself.

The gateway is the obvious candidate: it is a Python application doing what a purpose-built proxy does, and every request in the system passes through it.
This was predicted in [research.md](../specs/001-domain-services/research.md) decision 2, and the spec accepted it in advance (SC-001) so that the week would measure the cost rather than chase it.

**What has not been measured** is how much of the loss is the gateway and how much is the rest of the distribution.
Establishing that would mean running the load client directly against the checkout service, bypassing the gateway, and comparing.
That would be the first thing to do with more time, and until it is done, "the gateway is the bottleneck" remains a hypothesis supported by the shape of the numbers rather than a measurement.

### The stress result inverts, and the reason matters

| Stress, p95 | pipeline | service-based |
|---|---|---|
| SCAN_ITEM | 10.88ms | 490.93ms |
| COMPLETE_TRANSACTION | 2,726.74ms | **486.44ms** |
| COMPLETE_TRANSACTION p99 | 4,608.61ms | **807.08ms** |
| Error rate | 0.02% | **0.00%** |

Completion got five times faster, and the timeouts that cost weeks 2 and 4 three requests each disappeared.

This is not an improvement in completion.
Completion holds row locks while decrementing stock, and its cost under load is contention for those locks.
Only 37.6 transactions a second now reach the database instead of 124.5, so there is far less to contend over.

**The slowest operation got faster because the system got slower.**
Stated plainly because the number is flattering and the cause is not: the gateway is acting as an accidental rate limiter, and the database is no longer the bottleneck because it is no longer busy.

It is still a real observation about the architecture. Putting a chokepoint in front of a contended resource does protect that resource. It is simply not what this week was trying to do.

### Operational weight

Four processes instead of one.
Starting the system needs a script; stopping it needs a script that also sweeps the ports, because a leftover process holding 8080 would silently serve the next benchmark run.

Debugging crosses processes.
A failed scan could be the gateway, the checkout service, or the database, and the logs are in three files.

Forty-one files where week 4 had twenty-three.

### A failure mode that did not exist before

The gateway returns a 500 when a service is down.
The previous weeks had no equivalent: if the process was up, every endpoint worked, and if it was down, nothing did.

Now partial availability is possible, which is both a feature and a new thing to reason about.

## Part 3: Characteristics, revisited

| Characteristic | Week 4 (pipeline) | Week 5 (service-based) |
|---|---|---|
| Throughput | 123.6 tx/sec | **45.1 tx/sec** |
| Scan latency, default | 7.10ms p95 | **28.30ms p95** |
| Completion under stress | 2,726ms p95 | **486ms p95**, by being less busy |
| Error rate under stress | 0.02% | **0.00%** |
| Data integrity | held | held, both runs exact |
| Independent deployability | none | **each service starts alone** |
| Fault isolation | none | partial: one service down leaves others up |
| Boundary enforcement | convention | **structural, and tested** |
| Availability | one process, one database | four processes, one database, two single points of failure |
| Operational complexity | one command | four processes, scripts, three log files |

The availability row is the honest one.
More processes did not buy availability here.
The gateway must be up for anything to work, and the database must be up for anything to work, so the system has more moving parts and the same two critical ones.

## What the measurements showed

| | Default (10 stations, 60s) | Stress (100 stations, 120s) |
|---|---|---|
| Transactions | 2,714 (45.1/sec) | 4,588 (37.6/sec) |
| Items scanned | 28,337 (471.1/sec) | 48,447 (397.4/sec) |
| Error rate | 0.00% | 0.00% |
| START p95 | 29.05ms | 588.57ms |
| SCAN p95 | 28.30ms | 490.93ms |
| COMPLETE p95 | 30.58ms | 486.44ms |
| Scans dropped | 0 | 0 |
| Service errors logged | 0 | 0 |

Correctness held perfectly in both runs: all 2,000 SKUs exact, nothing negative, nothing unexplained.

This is also the first week with no sold-out SKU under stress.
Previous weeks exhausted SKU-000001 because they pushed enough volume to sell 10,000 units of it.
At a third of the throughput, nothing sold out.
Another consequence of being slower rather than of being better.

Popular items ranked SKU-000001 first in all four weeks with the same Zipf-shaped tail, which is the cross-week check the course asks for.
The ranking survived being split across two services that never speak, and being rebuilt from a timer instead of a queue.

## The question this week raises for the next one

Service-based architecture cost two thirds of the throughput and bought independent deployability, which this system did not need, since all four processes run on one machine against one database.

The weeks ahead are microservices and event-driven.
Microservices will add more boundaries and, done conventionally, a database per service.
The interesting question is whether the throughput lost here is a fixed tax on distribution, or whether it was specific to routing everything through one Python process.

The measurement that would answer it is the one named above: the load client pointed straight at the checkout service, with no gateway in the path.

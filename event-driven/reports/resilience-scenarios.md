# Resilience scenarios

Four measurements, each one run rather than reasoned about.
They are the argument for having added a broker at all: everything else this week either stayed the same or got slightly slower, and these are what was bought with that.

Every figure below is transcribed from a run of `./scripts/run_scenarios.sh`, whose raw output is in `reports/scenario-run.txt`.
The databases and both queues are reset before each scenario, so one scenario's backlog cannot explain another's result.

## The question each scenario asks

The system is now three processes that never speak to each other, coordinating through a broker that is itself a fourth process.
That is three new things that can be separately down.
A shopper should not be able to tell in any of the three cases, and no sale should be lost in any of them.

## Scenario 1: inventory is down

```bash
./scripts/stop_one.sh inventory
uv run python scripts/buy.py 20 3        # 20 baskets, 3 items each
./scripts/stop_one.sh inventory --start
./scripts/check_invariant.sh
```

| Measurement                     | Result  |
| ------------------------------- | ------- |
| Baskets paid for                | 20 / 20 |
| Receipts fully itemised         | 20 / 20 |
| Sales waiting in the broker     | 20      |
| Messages checkout still owed    | 0       |
| Sales waiting after the restart | 0       |
| Unexplained stock violations    | 0       |
| Announced by checkout           | 20      |
| Applied by inventory            | 20      |

Nothing was refused and nothing was lost.
The 20 sales sat in `inventory.sales` for the length of the outage and were applied within seconds of inventory starting.

Last week this was the scenario that returned HTTP 503 to the shopper, because paying meant calling inventory and waiting for its answer.

## Scenario 2: the broker is down

```bash
docker compose stop rabbitmq
uv run python scripts/buy.py 20 3
docker compose start rabbitmq
./scripts/check_invariant.sh
```

| Measurement                             | Result  |
| --------------------------------------- | ------- |
| Baskets paid for                        | 20 / 20 |
| Receipts fully itemised                 | 20 / 20 |
| Messages owed while the broker was down | 20      |
| Messages owed 25s after it returned     | 0       |
| Unexplained stock violations            | 0       |
| Applied by inventory                    | 20 / 20 |

This is the scenario that would catch a mistake in the design rather than in the code.
If anything in the payment path published to the broker, these 20 payments would have failed, and the fact that they did not is the evidence that the outbox row is written and the request ends.

The 20 owed messages are the outbox doing its job: the sales were committed facts in checkout's database, waiting for a transport that did not exist yet.

## Scenario 3: analytics is down

```bash
uv run python scripts/scan_latency.py 10       # baseline
./scripts/stop_one.sh analytics
uv run python scripts/scan_latency.py 30       # during the outage
./scripts/stop_one.sh analytics --start
```

| Measurement         | Analytics up | Analytics down |
| ------------------- | ------------ | -------------- |
| Scans completed     | 2,278        | 7,120          |
| Median scan latency | 4.13 ms      | 3.94 ms        |
| p95 scan latency    | 5.69 ms      | 5.72 ms        |

| Measurement                     | Result |
| ------------------------------- | ------ |
| Scans waiting in the broker     | 7,120  |
| Scans waiting after the restart | 0      |
| Scans lost                      | 0      |

Scan latency did not move, which is the result the scan path has produced every week since it was put behind a queue.

The second half is new this week.
Week 6 dropped scans published while analytics was down, because an HTTP post to a dead service has nowhere to go and retrying would have backed the queue up.
A scan message is published transiently, meaning the broker does not write it to disk, but it still lands in a durable queue and waits there.
All 7,120 scans arrived and the ranking caught up, so "a scan is allowed to be lost" has quietly become "a scan is lost only if the broker itself restarts".

## The lag that replaced the saga

```bash
./scripts/measure_lag.sh 10
```

| Measurement                               | Result  |
| ----------------------------------------- | ------- |
| Median delay from receipt to stock moving | 48.8 ms |
| Worst of ten                              | 80.0 ms |

This number did not exist before this week.
Until now paying reduced stock inside the request, so the delay was zero by construction.

It is almost exactly the relay's 50 ms poll interval, which is the honest reading: the delay is how long the sale waits to be picked up, not how long anything takes to do.
Polling every 5 ms would cut it to roughly that, at the cost of a hundred times as many queries against a table that is normally empty.

## What bounds the backlog

Two places hold work that has not been delivered, and they shed it differently.

Scans waiting to leave checkout sit in an in-memory queue capped at 50,000 (`MAX_PENDING` in `checkout/app/ingest.py`).
Above that, new scans are counted and dropped rather than blocking a sale.
At the 1,767 scans per second the stress run produces, that is about 28 seconds of total broker unavailability before the first scan is shed.

Sales waiting in the broker have no queue-level limit.
RabbitMQ stops accepting publishes when free disk falls below `disk_free_limit`, which is 50 MB on this broker against 971 GB free.
A sale message is a few hundred bytes, so the practical bound is the disk, and at this system's rate it is not a bound anyone would reach.

## What was verified and what was assumed

Verified by running it: all three outages, the lag figures, the scan latency comparison, the queue depths, and the invariant after each scenario.

Assumed: the disk bound above is read from the broker's configured limit and arithmetic, not from filling a disk.
The 28 second figure for scan shedding is the queue size divided by the stress run's measured scan rate, not something reproduced under a real 28 second outage.

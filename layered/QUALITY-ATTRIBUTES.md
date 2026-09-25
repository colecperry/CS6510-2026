# Quality Attributes and Architectural Characteristics

Week 2: layered implementation (Python, FastAPI, PostgreSQL, raw SQL via asyncpg).
Performance claims come from the two runs in `reports/`, compared against week 1's runs in `../monolith/reports/`.

Week 1 asked which characteristics the system must have.
The answer has not changed, because the contract has not changed.
This week asks a different question: what did splitting the same system into four layers actually buy, and what did it cost.

## Part 1: What the layering changed

### Maintainability went up, and it was the point

Week 1's analysis named this as the thing worth paying for:

> anything that tangles business rules into delivery mechanics is a cost I pay again
> the stock decrement and window logic should be readable without knowing FastAPI

That was aspirational last week and is now true.
`app/transactions/service.py` imports no FastAPI and contains no SQL.
`app/data/transactions_repo.py` contains SQL and knows nothing about why any of it is being run.

The concrete measure is `scan_item`.
In week 1 it was one 55-line route handler doing a cache lookup, a conditional UPDATE, a second query to distinguish 404 from 409, a line-item INSERT, an analytics call, and response assembly.
It is now a one-line route, about 25 lines of rules, and five named repository methods.
Nothing was deleted; it was sorted.

This matters beyond tidiness because the same contract gets rebuilt again in four more styles.
The checkout rules are now a thing that can be lifted into a service-based or event-driven implementation without dragging FastAPI along.

### Testability went from theoretical to real

Week 1 listed testability as a required characteristic and then shipped no tests.
That was not laziness - the rules lived inside route handlers, so testing them meant starting a web server and a database.

The services now take their repositories as constructor arguments, so a test can pass stand-ins that hold data in plain Python:

```python
service = TransactionService(FakeTransactionsRepo(state), CATALOG, FakeAnalyticsService())
with pytest.raises(ConflictError):
    await service.scan("tx-1", "SKU-1")
```

Seven tests, no Docker, no HTTP, 0.09 seconds.
They cover the decisions that are easy to get wrong and invisible in a load test: unknown SKU, closed basket, missing basket, empty basket, already-paid basket, and that the ranking rebuilds on schedule rather than on every scan.

### One coupling removed outright

Week 1's `ConflictError` carried `status_code = 409` on the exception class.
The checkout logic therefore knew how its failures would be rendered over HTTP, which is delivery knowledge sitting in a business rule.

`app/errors.py` is now framework-free, and `app/api/error_handlers.py` is the only file in the project that mentions a status code.
"A closed basket is a conflict" and "conflicts are 409s" are now separate statements in separate layers.

### Consistency became explicit rather than accidental

Week 1's data integrity argument rested on three things: guards folded into mutating statements, an atomic decrement, and one database transaction around completion.
All three survived, but they are now in one place rather than spread across a route handler.
`TransactionsRepo.complete()` holds the entire `async with conn.transaction()` block, so the all-or-nothing guarantee is a property of one method that can be read in full on one screen.

That is a real robustness gain. In week 1 the guarantee was an emergent property of how a route handler happened to be written, and could have been broken by an innocent-looking refactor.

## Part 2: What it cost

### Performance: nothing measurable

| | monolith | layered | change |
|---|---|---|---|
| Default, transactions/sec | 98.3 | 99.7 | +1.4% |
| Default, SCAN p95 | 9.68ms | 9.49ms | -0.19ms |
| Default, COMPLETE p95 | 21.48ms | 20.95ms | -0.53ms |
| Stress, transactions/sec | 117.0 | 115.8 | -1.0% |
| Stress, SCAN p95 | 18.39ms | 19.37ms | +0.98ms |
| Stress, COMPLETE p95 | 2,722.91ms | 2,712.42ms | -10.5ms |
| Stress, COMPLETE p99 | 4,486.00ms | 4,410.02ms | -76ms |

Every difference is inside run-to-run noise, and the default run came out marginally faster rather than slower.

This is the expected result, but it was not guaranteed, and two decisions are why.

**Services are constructed once, not per request.**
The idiomatic FastAPI approach is `Depends`, which resolves on every request.
At 1,213 scans per second that is real work repeated for objects that never change.
`main.py` builds every repository and service inside the lifespan and stores them in `app/api/wiring.py`, so a request pays nothing for dependency resolution.

**The business layers return the same Pydantic models the API serialises.**
The stricter alternative is a parallel set of domain types that the API layer maps at the edge.
For this system those types would have had identical fields to the wire models, so the mapping would have been pure overhead on the hottest path in the project.
The cost of skipping it is that the business layers know the response field names, which is a real coupling, mitigated by `schemas.py` sitting above all four layers rather than inside `api/`.

### The layering did not fix the actual bottleneck

Completion is still where the system falls over: p95 of 2.7 seconds under stress, against 21ms at the default load.

That was never an architecture problem.
It is the price of the data integrity guarantee - completion holds a database transaction while decrementing stock row by row, and at 100 stations with Zipf-skewed demand those row locks queue on a handful of popular SKUs.
Reorganising the code into layers does not change how long a lock is held.

Worth being clear about, because it would be easy to present "we restructured the system" and "the system is slow in one place" as connected. They are not.
Week 1's closing note still stands as the thing worth doing: collapsing completion's statements into fewer round trips would shorten lock hold time, which is what the queueing is made of.

### Three stress-run timeouts

The load client reported 3 failed completions out of 13,939, where week 1 reported zero.
Investigated rather than assumed:

- The server logged no errors or exceptions.
- The database holds 13,939 `COMPLETED` transactions and none left open.
- The invariant check shows zero unexplained violations.

All 13,939 completions succeeded server-side.
The client's per-request timeout is 10 seconds and the slowest completion took 9.90 seconds, so three requests were abandoned by the client on an already-slow tail.

It remains an honest difference from week 1's 0.00%.
Completion p95 and p99 are both slightly better than week 1, so the tail did not get worse in general - three requests happened to land past the cutoff on this run where week 1's worst landed just under it.

### A cost that is not visible in any measurement

There are now 14 Python files where week 1 had 12, and following a single request means opening three files instead of one.
For a system this size that is a genuine loss of directness, and it is the honest counterweight to the maintainability gain.

The trade only pays off because this contract gets rebuilt four more times.
For a system that was never going to change again, week 1's structure was the better answer.

## Part 3: Characteristics, revisited

Week 1 listed eleven required characteristics.
Most are properties of the contract and the database, so the layering left them untouched.
These are the ones that moved:

| Characteristic | Week 1 | Week 2 |
|---|---|---|
| Maintainability | Rules tangled into route handlers | Rules in their own layer, no framework imports |
| Testability | Required, but no tests possible without a server | 7 tests, no database, 0.09s |
| Data integrity | Held, but as an emergent property of a route handler | Held, and localised in one repository method |
| Modifiability | Changing storage meant touching every route | All SQL behind four repository classes |
| Performance | Baseline | Unchanged, within noise |
| Availability | Single process, single database | Unchanged - layers are not processes |

That last row is worth stating plainly.
A layered architecture is an organisation of code, not of deployment.
It is still one process against one database, so the single point of failure week 1 identified is exactly as present as it was.
Nothing about this week's work moves the 99.9% availability target, and the later weeks are presumably where that gets attacked.

## What the measurements showed

| | Default (10 stations, 60s) | Stress (100 stations, 120s) | Change |
|---|---|---|---|
| Transactions | 5,987 (99.7/sec) | 13,936 (115.8/sec) | +16% |
| Items scanned | 62,947 (1,048.2/sec) | 146,060 (1,213.4/sec) | +16% |
| Error rate | 0.00% | 0.02% | 3 client timeouts |
| START_TRANSACTION p95 | 3.67ms | 7.13ms | 1.9x |
| SCAN_ITEM p95 | 9.49ms | 19.37ms | 2.0x |
| COMPLETE_TRANSACTION p95 | 20.95ms | 2,712.42ms | 129x |
| COMPLETE_TRANSACTION p99 | 29.10ms | 4,410.02ms | 152x |

The shape is identical to week 1: throughput barely moves under ten times the load because the system was already saturated, and the pain concentrates almost entirely in completion.

Correctness held in both runs.
Default: all 2,000 SKUs matched the invariant exactly.
Stress: 1,999 exact, and SKU-000001 sold out and floored at zero, which is the same expected edge case week 1 documented, since the contract defines no insufficient-stock response.

Popular items ranked SKU-000001 first in both weeks with the same Zipf-shaped tail.
That is the cross-week check the course README asks for, and it confirms the analytics behaviour survived being moved into its own layer.

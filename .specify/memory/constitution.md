<!--
Sync Impact Report
==================
Version change: none -> 1.0.0 (initial ratification; template placeholders replaced
with the practices already observed in monolith/, layered/ and pipeline/)

Principles defined:
  I.   The Contract Is Fixed
  II.  Weeks Must Stay Comparable
  III. Correctness Before Speed
  IV.  Evidence, Not Assertion
  V.   Readable By Someone Who Has Not Seen It

Sections added:
  Technology And Layout Constraints  (SECTION_2)
  Weekly Workflow                    (SECTION_3)
  Governance

Removed sections: none

Deferred items: none. RATIFICATION_DATE is the first commit in this repository
(2026-08-24); no earlier adoption date exists.

This report is scratch material for reviewing the amendment and should be deleted
before the file is committed.
-->

# CS6510 Self-Checkout Constitution

The same self-checkout API is re-implemented in a different architecture style each week,
in its own folder, and measured with the same unmodified load client.
These principles are what make that comparison mean anything.
They are derived from how `monolith/`, `layered/` and `pipeline/` already work, not from
aspiration.

## Core Principles

### I. The Contract Is Fixed

Every week MUST implement the same seven endpoints defined in
`spec/self-checkout-openapi.yaml`, with identical request and response shapes, identical
field names on the wire, and identical status codes for every error path.

The Java load client in `load-client/` MUST NOT be modified, for any reason, in any week.

A week is free to do anything it likes internally - queues, background workers, separate
processes, events - but the client-facing behaviour MUST NOT change.

**Rationale**: The client and the contract are the controls in the experiment. The moment
either moves, week-to-week differences stop being attributable to the architecture.

### II. Weeks Must Stay Comparable

Each week MUST live in its own top-level folder, with its own Postgres container, its own
container name, and its own host port.

A finished week MUST NOT be edited to accommodate a later one. Corrections to a finished
week are permitted only when they change no behaviour, and the commit MUST say so.

Dependency versions MUST be pinned identically across weeks, including transitive
dependencies that sit on the request path. Where a resolver would otherwise drift, the
version MUST be pinned exactly and the pin MUST carry a comment saying why.

Load-test parameters MUST match the previous weeks: default and stress, with the same
station counts and durations.

**Rationale**: A latency difference must have exactly one possible cause. A library upgrade
or a changed parameter silently becomes an alternative explanation, and the week's headline
result becomes unfalsifiable.

### III. Correctness Before Speed

For every SKU, `initial_stock - final_stock` MUST equal the units sold in completed
transactions, and stock MUST never go negative. This invariant MUST be checked after every
load run, and the result MUST be reported.

Any check that guards a state change MUST live inside the statement that performs it. A
read, then a decision, then a write, as separate statements against shared data, is
forbidden.

Inventory MUST be exact. Analytics MAY lag, and the amount it may lag MUST be stated.

**Rationale**: This system tracks money. Of the available failure modes, degrading into
latency is the one to choose; degrading into a wrong stock count is not.

### IV. Evidence, Not Assertion

Every week MUST ship two load-client JSON reports in its `reports/` folder, committed, from
the default and stress parameters.

Every figure quoted in any write-up MUST be checked against those reports rather than
recalled. Where practical the check MUST be mechanical.

Every week MUST state explicitly what was verified and what was assumed.

A regression MUST be reported with its magnitude. An explanation offered without
measurement MUST be labelled as a hypothesis.

**Rationale**: The reports are the only thing that distinguishes an architecture claim from
an opinion. A number quoted from memory, or a regression quietly omitted, costs the whole
write-up its credibility.

### V. Readable By Someone Who Has Not Seen It

Every source file MUST open with two or three lines saying what it is for and where it sits
in the system.

A comment MUST make sense to someone reading only the file it is in. It MUST NOT depend on
a detail from another file, a database schema, or a library's internals without first
saying what that is in plain words. A comment MUST say why rather than what, and MUST NOT
run past two lines.

Every week MUST ship a `README.md` covering setup and exact reproduction commands, and a
`QUALITY-ATTRIBUTES.md` analysing what the architecture bought and what it cost.

The em dash character MUST NOT appear in any file.

**Rationale**: Six implementations of one system are only worth having if each can be read
on its own. A comment that requires three other files open is a comment that will be wrong
within a week.

## Technology And Layout Constraints

Python 3.12 or later, managed with `uv`. Raw SQL via `asyncpg`; no ORM.

Each week's folder MUST be self-contained: its own `pyproject.toml`, `docker-compose.yml`,
`db/schema.sql`, seed script and reset script. A week MUST NOT import code from, or depend
on the running state of, another week.

`db/schema.sql` MUST stay byte-identical across weeks unless a week's architecture genuinely
requires a change, in which case the change and its justification MUST be documented.

Starting data MUST be identical: 2,000 SKUs, 10,000 units each, the same price formula.

Postgres host ports are allocated one per week and never reused.

## Weekly Workflow

A week starts by copying the most appropriate finished week, not by writing from scratch.
The copy MUST be verified byte-identical before any change is made, so that every later
difference is deliberate.

Implementation proceeds one step at a time, with the reasoning stated before the change and
the outcome explained after it.

Before a week is declared done: its tests MUST pass, both load runs MUST complete, the stock
invariant MUST hold, and the verified-versus-assumed statement MUST be written.

Commits mark logical checkpoints, not individual steps. A commit at which the code does not
run MUST say so.

## Governance

This constitution governs every week in this repository and supersedes habit or convenience.

Amendments MUST be made by updating this file, with the version incremented per the policy
below and the rationale recorded in the commit message.

Versioning follows semantic versioning:

- MAJOR: a principle is removed or redefined in a way that invalidates finished weeks.
- MINOR: a principle or section is added, or guidance materially expanded.
- PATCH: clarification or wording, with no change in meaning.

Compliance is reviewed when a week is declared done. A week that violates a principle MUST
either be corrected or MUST record the violation and its justification in that week's
`QUALITY-ATTRIBUTES.md`. Silent deviation is the one thing this document exists to prevent.

**Version**: 1.0.0 | **Ratified**: 2026-08-24 | **Last Amended**: 2026-10-13

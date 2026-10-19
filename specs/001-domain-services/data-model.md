# Data Model: Service-Based Self-Checkout

**Date**: 2026-10-13
**Feature**: [spec.md](spec.md)

The schema is unchanged from the previous weeks, byte for byte, as the constitution requires for comparability.
What is new is that several processes now read and write it, so this document records who owns what.

---

## Entities

### Catalogue item

What the shop sells. Fixed for the life of a run.

| Field | Type    | Notes                          |
|-------|---------|--------------------------------|
| sku   | text    | identifier, unique             |
| name  | text    | shown on receipts and rankings |
| price | decimal | charged at the time of sale    |

2,000 rows, loaded once at startup. Nothing in the contract can change it, so every service may hold its own in-memory copy without risk of going stale.

### Stock level

How many units remain.

| Field | Type    | Notes                    |
|-------|---------|--------------------------|
| sku   | text    | identifies the item      |
| qty   | integer | must never go below zero |

Starts at 10,000 per item. Reduced only when a basket is paid for.

### Basket

One shopper's visit to a station.

| Field          | Type                    | Notes                 |
|----------------|-------------------------|-----------------------|
| transaction_id | text                    | identifier, generated |
| station_id     | text                    | which checkout        |
| status         | open / paid / abandoned |                       |
| item_count     | integer                 | running count         |
| running_total  | decimal                 | running total         |
| started_at     | timestamp               |                       |
| completed_at   | timestamp               | set when paid         |

**States**: a basket opens, accumulates items while open, and becomes paid.
Once paid it accepts nothing further.
Only an open, non-empty basket may be paid for.

### Basket line

One scanned unit within a basket.

| Field          | Type    | Notes               |
|----------------|---------|---------------------|
| transaction_id | text    | which basket        |
| sku            | text    | which item          |
| name           | text    | copied at scan time |
| unit_price     | decimal | copied at scan time |

Name and price are copied rather than looked up later, so a receipt always shows what was actually charged.
Several lines for the same item collapse into one receipt line carrying a quantity.

### Scan record

One scanned unit, recorded separately from the basket and used only for reporting.

| Field | Type    | Notes                           |
|-------|---------|---------------------------------|
| seq   | integer | position in one global ordering |
| sku   | text    | which item                      |

Kept separate from basket lines on purpose, so old records can be discarded without touching anything a receipt needs.
Discarded once no future ranking can include them.

### Ranking position

One row of the published ranking.

| Field      | Type    | Notes                       |
|------------|---------|-----------------------------|
| rank       | integer | position, unique            |
| sku        | text    | which item                  |
| name       | text    |                             |
| scan_count | integer | times scanned in the window |

Replaced wholesale each time the ranking is rebuilt, never appended to.

### Ranking window

Single row of bookkeeping recording which stretch of scanning produced the current ranking.

| Field                    | Type      | Notes                           |
|--------------------------|-----------|---------------------------------|
| window_size              | integer   | how many scans a ranking covers |
| slide_interval           | integer   | how many scans between rebuilds |
| window_start, window_end | integer   | the stretch counted             |
| computed_at              | timestamp |                                 |

---

## Ownership

This is the part that is new this week.
Services coordinate only through this table.

| Entity           | Selling     | Stock    | Reporting         |
|------------------|-------------|----------|-------------------|
| Catalogue item   | read        | read     | read              |
| Stock level      | **write**   | **read** |                   |
| Basket           | read, write |          |                   |
| Basket line      | read, write |          |                   |
| Scan record      | **write**   |          | **read, discard** |
| Ranking position |             |          | read, write       |
| Ranking window   |             |          | read, write       |

### The two shared entities

**Stock level** is written by selling when a basket is paid for, and read by stock to report what is running low.
No call passes between them.

**Scan record** is written by selling as items are scanned, and read by reporting to build the ranking.
This is how a scan reaches the ranking without the two services ever speaking.

Both are the intended mechanism, not a design fault.
In this style of architecture the database is the coupling point.

### What is not shared

Reporting never reads baskets or basket lines, and selling never reads the ranking.
The same scan is recorded twice and independently: once as a basket line, which is permanent and belongs to the receipt, and once as a scan record, which is temporary and belongs to reporting.
That duplication is what allows old scan records to be discarded safely.

---

## Rules that must hold

Taken from the spec's functional requirements.

| Rule                                                                          | From   |
|-------------------------------------------------------------------------------|--------|
| For every item, stock sold equals stock reduced. Stock never goes below zero. | FR-017 |
| Marking a basket paid and reducing stock happen together or not at all.       | FR-012 |
| Two shoppers paying at once for the same item lose neither reduction.         | FR-018 |
| An item may only be added to an open basket.                                  | FR-013 |
| An empty basket may not be paid for.                                          | FR-013 |
| A basket may not be paid for twice.                                           | FR-013 |
| Recording a scan for reporting does not delay the shopper.                    | FR-016 |

The first three are the ones the split puts at risk, because the writes now come from a process that no longer shares memory with anything else.
They are preserved by keeping the guard inside the writing statement and the whole payment inside one database transaction, exactly as the previous weeks did.

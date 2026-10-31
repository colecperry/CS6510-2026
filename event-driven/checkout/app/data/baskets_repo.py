# All SQL for baskets and the lines within them.
#
# Every check that guards a write is folded into the writing statement, so two shoppers racing on the same basket cannot both win.
# Nothing here reduces stock: that table belongs to the inventory service, which is told that a sale happened rather than asked to allow one. See outbox_repo.py for how the telling is made reliable.
# Two methods take a connection rather than using the pool, because the caller runs them inside one transaction alongside a write to another table in this database.
from datetime import datetime
from typing import NamedTuple

import asyncpg


# A paid basket, as the receipt reports it.
class CompletedRow(NamedTuple):
    station_id: str
    item_count: int
    running_total: float
    started_at: datetime
    completed_at: datetime


# One basket, as the API reports it.
class TransactionRow(NamedTuple):
    transaction_id: str
    station_id: str
    status: str
    item_count: int
    running_total: float
    started_at: datetime


# The two running totals a scan updates.
class ScanCounters(NamedTuple):
    item_count: int
    running_total: float


# Just enough to explain why a write was refused.
class TransactionState(NamedTuple):
    status: str
    item_count: int


# One line of a receipt: several scans of the same item, counted up.
class ReceiptLineRow(NamedTuple):
    sku: str
    name: str
    unit_price: float
    quantity: int


class BasketsRepo:
    def __init__(self, pool: asyncpg.Pool):
        self._pool = pool

    async def start(self, station_id: str) -> TransactionRow:
        """Creates a new empty basket and returns it."""
        # RETURNING hands back the generated id and column defaults, so there is no second query to read the row we just wrote.
        row = await self._pool.fetchrow(
            """
            INSERT INTO transactions (station_id)
            VALUES ($1)
            RETURNING transaction_id, station_id, status, item_count, running_total, started_at
            """,
            station_id,
        )
        return _to_transaction_row(row)

    async def bump_counters(self, transaction_id: str, unit_price: float) -> ScanCounters | None:
        """Adds one item to the basket's totals. None if the basket was not open."""
        # The check and the update are one statement, so a payment cannot slip in between them and leave the receipt missing an item the shopper was charged for. Postgres holds the row for the length of the statement, and whichever of the two runs second sees the other's result.
        # Week 6 needed a second guard here, because paying spanned two transactions with a network call in between and left a window this status could not cover. Paying is one transaction now, so the status is enough on its own.
        row = await self._pool.fetchrow(
            """
            UPDATE transactions
            SET item_count = item_count + 1, running_total = running_total + $2
            WHERE transaction_id = $1 AND status = 'OPEN'
            RETURNING item_count, running_total
            """,
            transaction_id,
            unit_price,
        )
        if row is None:
            return None                  # not open, no such basket, or mid-payment
        return ScanCounters(row["item_count"], float(row["running_total"]))

    async def state_of(self, transaction_id: str) -> TransactionState | None:
        """Reads a basket's status and size. None if there is no such basket."""
        # Answers "why was that refused?" - missing basket, closed one, or empty one. Only runs after something already failed, so a normal scan never pays for it.
        row = await self._pool.fetchrow(
            "SELECT status, item_count FROM transactions WHERE transaction_id = $1",
            transaction_id,
        )
        if row is None:
            return None
        return TransactionState(row["status"], row["item_count"])

    async def add_line(
        self, transaction_id: str, sku: str, name: str, unit_price: float
    ) -> None:
        """Records one scanned unit. The receipt is built from these rows."""
        # Name and price are copied in rather than looked up later, so a receipt always shows what the item cost at the time it was sold.
        await self._pool.execute(
            """
            INSERT INTO transaction_items (transaction_id, sku, name, unit_price)
            VALUES ($1, $2, $3, $4)
            """,
            transaction_id,
            sku,
            name,
            unit_price,
        )

    async def finalise(
        self, conn: asyncpg.Connection, transaction_id: str
    ) -> CompletedRow | None:
        """Marks a basket paid and returns what the receipt needs. None if it was not an open basket with something in it."""
        # Both conditions a payment can fail on live inside the statement, so two shoppers paying at once cannot both be told yes. Whichever runs second finds the status already COMPLETED and gets nothing back.
        # Takes a connection because the caller is also writing the outbox row, and a sale marked paid without its announcement would never reduce stock.
        row = await conn.fetchrow(
            """
            UPDATE transactions
            SET status = 'COMPLETED', completed_at = now()
            WHERE transaction_id = $1 AND status = 'OPEN' AND item_count > 0
            RETURNING station_id, item_count, running_total, started_at, completed_at
            """,
            transaction_id,
        )
        if row is None:
            return None
        return CompletedRow(
            station_id=row["station_id"],
            item_count=row["item_count"],
            running_total=float(row["running_total"]),
            started_at=row["started_at"],
            completed_at=row["completed_at"],
        )

    async def receipt_lines(
        self, conn: asyncpg.Connection, transaction_id: str
    ) -> list[ReceiptLineRow]:
        """Collapses a basket's scans into receipt lines, three scans of one item becoming quantity 3."""
        # Ordered by SKU so every payment takes items in the same order. Two shoppers buying the same pair can then never each hold what the other needs.
        rows = await conn.fetch(
            """
            SELECT sku, name, unit_price, count(*) AS quantity
            FROM transaction_items
            WHERE transaction_id = $1
            GROUP BY sku, name, unit_price
            ORDER BY sku
            """,
            transaction_id,
        )
        return [
            ReceiptLineRow(r["sku"], r["name"], float(r["unit_price"]), r["quantity"])
            for r in rows
        ]

    async def fetch(self, transaction_id: str) -> TransactionRow | None:
        """Reads one basket. None if there is no such basket."""
        row = await self._pool.fetchrow(
            """
            SELECT transaction_id, station_id, status, item_count, running_total, started_at
            FROM transactions
            WHERE transaction_id = $1
            """,
            transaction_id,
        )
        if row is None:
            return None
        return _to_transaction_row(row)


def _to_transaction_row(row: asyncpg.Record) -> TransactionRow:
    # float() so Postgres Decimals never escape the data layer.
    return TransactionRow(
        transaction_id=row["transaction_id"],
        station_id=row["station_id"],
        status=row["status"],
        item_count=row["item_count"],
        running_total=float(row["running_total"]),
        started_at=row["started_at"],
    )

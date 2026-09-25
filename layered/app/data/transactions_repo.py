# All SQL for the transactions and transaction_items tables.
#
# The transactions layer calls these methods and never writes SQL itself.
# Every check that guards a write is folded into the writing statement, so
# two checkouts racing on the same basket cannot both win.
from datetime import datetime
from typing import NamedTuple

import asyncpg


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


# Everything needed to print a receipt, gathered while paying.
class CompletedTransaction(NamedTuple):
    station_id: str
    item_count: int
    total_amount: float
    started_at: datetime
    completed_at: datetime
    lines: list[ReceiptLineRow]


class TransactionsRepo:
    def __init__(self, pool: asyncpg.Pool):
        self._pool = pool

    async def start(self, station_id: str) -> TransactionRow:
        """Creates a new empty basket and returns it."""
        # RETURNING hands back the generated id and column defaults, so there
        # is no second query to read the row we just wrote.
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
        """Adds one item to the basket's totals. None if it was not open."""
        # "Still open?" is part of the UPDATE, not a separate SELECT, so no
        # other request can close the basket between the check and the write.
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
            return None
        return ScanCounters(row["item_count"], float(row["running_total"]))

    async def state_of(self, transaction_id: str) -> TransactionState | None:
        """Reads a basket's status and size. None if there is no such basket."""
        # Only called after a write was refused, to work out which error to
        # report. Not on the normal path, so the extra query costs nothing
        # in the common case.
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
        # Name and price are copied in rather than looked up later, so a
        # receipt always shows what the item cost at the time it was sold.
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

    async def complete(self, transaction_id: str) -> CompletedTransaction | None:
        """Pays for the basket and takes its items out of stock.

        None if the basket could not be paid for; ask state_of() why.
        """
        async with self._pool.acquire() as conn:
            # One database transaction around all of it. The basket can
            # never end up marked paid without the stock coming down, or
            # the stock come down without the basket being marked paid.
            async with conn.transaction():
                # Both rules live in the UPDATE: still open, and not empty.
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

                # Collapse the scanned units into one line per item.
                line_rows = await conn.fetch(
                    """
                    SELECT sku, name, unit_price, count(*) AS quantity
                    FROM transaction_items
                    WHERE transaction_id = $1
                    GROUP BY sku, name, unit_price
                    ORDER BY sku
                    """,
                    transaction_id,
                )

                # Always in SKU order. Two checkouts holding the same two
                # items could otherwise take them in opposite orders and
                # sit waiting on each other forever.
                #
                # GREATEST(..., 0) because the contract has no "out of
                # stock" response, so a hot item floors at zero instead.
                for line in line_rows:
                    await conn.execute(
                        "UPDATE stock SET qty = GREATEST(qty - $2, 0) WHERE sku = $1",
                        line["sku"],
                        line["quantity"],
                    )

        # Built after the connection is handed back, so the basket's rows
        # are not held any longer than the writes need.
        return CompletedTransaction(
            station_id=row["station_id"],
            item_count=row["item_count"],
            total_amount=float(row["running_total"]),
            started_at=row["started_at"],
            completed_at=row["completed_at"],
            lines=[
                ReceiptLineRow(
                    sku=line["sku"],
                    name=line["name"],
                    unit_price=float(line["unit_price"]),
                    quantity=line["quantity"],
                )
                for line in line_rows
            ],
        )

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

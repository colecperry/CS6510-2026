# Reading stock levels.
#
# The one table two services share in both directions: checkout reduces stock when a basket is paid for, inventory reads it here to report what is low.
# Neither calls the other; the database is the hand-off.
#
# The reduction itself lives in baskets_repo, inside the payment transaction, because marking a basket paid and taking its items out of stock have to happen together or not at all.
from typing import NamedTuple

import asyncpg


# One item currently below the threshold.
class LowStockRow(NamedTuple):
    sku: str
    name: str
    qty: int


class StockRepo:
    def __init__(self, pool: asyncpg.Pool):
        self._pool = pool

    async def below_threshold(self, threshold: int) -> list[LowStockRow]:
        """Reads every item under a stock level, emptiest first."""
        rows = await self._pool.fetch(
            """
            SELECT c.sku, c.name, s.qty
            FROM stock s
            JOIN catalog c ON c.sku = s.sku
            WHERE s.qty < $1
            ORDER BY s.qty ASC
            """,
            threshold,
        )
        return [LowStockRow(r["sku"], r["name"], r["qty"]) for r in rows]

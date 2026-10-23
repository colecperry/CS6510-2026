# Every statement that touches this service's stock table.
#
# Stock exists here and nowhere else. Last week checkout reduced it directly inside the payment transaction; it now has no access to this database and must ask, which is why a reduction arrives as a request rather than as a write.
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

    async def apply_delta(self, conn: asyncpg.Connection, lines: list[tuple[str, int]], sign: int) -> None:
        """Moves stock by the given quantities. sign is -1 to sell, +1 to put back."""
        # Takes a connection rather than using the pool, so the caller can run this inside the same transaction that records the request as applied. Doing them separately would let one survive without the other.
        for sku, qty in lines:
            # GREATEST floors the result at zero rather than letting the table's CHECK constraint fire. The contract defines no out-of-stock response, so an oversold item stops at zero, as in every previous week.
            await conn.execute(
                "UPDATE stock SET qty = GREATEST(0, qty + $2) WHERE sku = $1",
                sku,
                sign * qty,
            )

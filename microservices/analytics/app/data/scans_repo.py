# Every statement that touches the scan log, this service's record of what was scanned.
#
# Checkout cannot write here any more. Scans arrive as batched HTTP posts and are inserted by this service, which is why reporting can fall behind without a shopper ever waiting.
from typing import NamedTuple

import asyncpg


# How many times one item was scanned inside a window.
class ScanCount(NamedTuple):
    sku: str
    scan_count: int


class ScansRepo:
    def __init__(self, pool: asyncpg.Pool):
        self._pool = pool

    async def record_scans(self, skus: list[str]) -> int:
        """Records a batch of scans and returns the highest sequence number used."""
        # Every scan gets a number from one counter, so "the last 1000 scans" becomes a simple range check later. The whole batch goes in as one statement rather than one per scan.
        return await self._pool.fetchval(
            """
            WITH inserted AS (
                INSERT INTO scan_log (sku)
                SELECT unnest($1::text[])      -- turns the list into rows
                RETURNING seq
            )
            SELECT max(seq) FROM inserted      -- the newest number of the batch
            """,
            skus,
        )

    async def count_window(self, start: int, end: int, cap: int) -> list[ScanCount]:
        """Counts scans per item inside a window, most scanned first."""
        rows = await self._pool.fetch(
            """
            SELECT sku, count(*) AS scan_count
            FROM scan_log
            WHERE seq > $1 AND seq <= $2
            GROUP BY sku
            ORDER BY scan_count DESC
            LIMIT $3
            """,
            start,
            end,
            cap,
        )
        return [ScanCount(r["sku"], r["scan_count"]) for r in rows]

    async def prune_before(self, conn: asyncpg.Connection, seq: int) -> None:
        """Drops scans too old for any future window to include."""
        # Takes a connection so the caller can prune in the same transaction that publishes the ranking those scans produced. Without this the log would grow for the life of the run.
        await conn.execute("DELETE FROM scan_log WHERE seq <= $1", seq)

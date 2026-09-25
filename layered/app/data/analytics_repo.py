# All SQL for the reporting tables: the scan log, the popularity ranking,
# and the low-stock view over the stock table.
#
# The analytics layer calls these methods and never writes SQL itself.
from datetime import datetime
from typing import NamedTuple

import asyncpg


# How many times one item was scanned inside the current window.
class ScanCount(NamedTuple):
    sku: str
    scan_count: int


# Which stretch of scans the published ranking was built from.
class WindowState(NamedTuple):
    window_size: int
    slide_interval: int
    window_start: int
    window_end: int
    computed_at: datetime


# One row of the published ranking.
class PopularItemRow(NamedTuple):
    rank: int
    sku: str
    name: str
    scan_count: int


# One item currently below the stock threshold.
class LowStockRow(NamedTuple):
    sku: str
    name: str
    qty: int


class AnalyticsRepo:
    def __init__(self, pool: asyncpg.Pool):
        self._pool = pool

    async def log_scan(self, sku: str) -> int:
        """Records one scan and returns its position in the global sequence."""
        # Numbering every scan from one sequence is what makes "the last
        # 1000 scans" a plain range of numbers later on.
        return await self._pool.fetchval(
            "INSERT INTO scan_log (sku) VALUES ($1) RETURNING seq", sku
        )

    async def claim_recompute(self, scan_seq: int) -> tuple[int, int] | None:
        """Tries to win the right to rebuild the ranking. None if not due yet.

        Returns the (start, end) of the window the winner should count.
        """
        # The "is it due?" test is part of the UPDATE, so with 100 stations
        # scanning at once exactly one of them can win. Everyone else gets
        # nothing back and moves on.
        row = await self._pool.fetchrow(
            """
            UPDATE popularity_state
            SET window_start = GREATEST(0, $1 - window_size),
                window_end = $1,
                computed_at = now()
            WHERE $1 - window_end >= slide_interval
            RETURNING window_start, window_end
            """,
            scan_seq,
        )
        if row is None:
            return None
        return row["window_start"], row["window_end"]

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

    async def publish_ranking(self, ranked: list[PopularItemRow], prune_before: int) -> None:
        """Swaps in a new ranking and drops scans that can never count again."""
        async with self._pool.acquire() as conn:
            # One transaction, so a reader never catches the ranking
            # half-replaced.
            async with conn.transaction():
                await conn.execute("DELETE FROM popularity_snapshot")
                for item in ranked:
                    await conn.execute(
                        """
                        INSERT INTO popularity_snapshot (rank, sku, name, scan_count)
                        VALUES ($1, $2, $3, $4)
                        """,
                        item.rank,
                        item.sku,
                        item.name,
                        item.scan_count,
                    )
                # Scans behind the window can never be counted again, so
                # drop them rather than letting scan_log grow forever.
                await conn.execute("DELETE FROM scan_log WHERE seq <= $1", prune_before)

    async def window_state(self) -> WindowState:
        """Reads which window the published ranking covers."""
        # Always a single row, id = 1.
        row = await self._pool.fetchrow(
            """
            SELECT window_size, slide_interval, window_start, window_end, computed_at
            FROM popularity_state
            WHERE id = 1
            """
        )
        return WindowState(
            row["window_size"],
            row["slide_interval"],
            row["window_start"],
            row["window_end"],
            row["computed_at"],
        )

    async def top_items(self, limit: int) -> list[PopularItemRow]:
        """Reads the published ranking, highest first."""
        # Already ranked and trimmed when it was published, so this just
        # takes the first few rows.
        rows = await self._pool.fetch(
            "SELECT rank, sku, name, scan_count FROM popularity_snapshot ORDER BY rank LIMIT $1",
            limit,
        )
        return [PopularItemRow(r["rank"], r["sku"], r["name"], r["scan_count"]) for r in rows]

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

# The scan log and the ranking built from it.
#
# This is how a scan reaches reporting without the two services speaking.
# Checkout writes scans here as they happen; analytics reads them, publishes a ranking, and discards scans too old for any future ranking to include.
from datetime import datetime
from typing import NamedTuple

import asyncpg


# How many times one item was scanned inside a window.
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


class ScansRepo:
    def __init__(self, pool: asyncpg.Pool):
        self._pool = pool

    # ---- written by checkout ----

    async def log_scans(self, skus: list[str]) -> int:
        """Records a batch of scans and returns the highest sequence number."""
        # Every scan gets a number from one counter, so "the last 1000 scans" becomes a simple range check later.
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

    # ---- read by analytics ----

    async def claim_rebuild_if_due(self) -> tuple[int, int] | None:
        """Claims the right to rebuild the ranking, if enough scans have passed.

        Returns the stretch of scans to count, or None if one is not due.
        """
        # Asking how far the log has got, deciding whether that is far enough, and claiming the rebuild all happen in one statement. Two polls can therefore never both claim the same rebuild, and a poll costs one round trip rather than two.
        row = await self._pool.fetchrow(
            """
            UPDATE popularity_state
            SET window_start = GREATEST(0, (SELECT COALESCE(max(seq), 0) FROM scan_log) - window_size),
                window_end = (SELECT COALESCE(max(seq), 0) FROM scan_log),
                computed_at = now()
            WHERE (SELECT COALESCE(max(seq), 0) FROM scan_log) - window_end >= slide_interval
            RETURNING window_start, window_end
            """
        )
        if row is None:
            return None                  # not due yet
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
            # The old ranking is deleted before the new one is written, so this has to be all-or-nothing or a reader could catch it empty.
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
                # No future window reaches back this far, so these rows can go. Without this the scan log would grow forever.
                await conn.execute("DELETE FROM scan_log WHERE seq <= $1", prune_before)

    async def window_state(self) -> WindowState:
        """Reads which window the published ranking covers."""
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
        rows = await self._pool.fetch(
            "SELECT rank, sku, name, scan_count FROM popularity_snapshot ORDER BY rank LIMIT $1",
            limit,
        )
        return [PopularItemRow(r["rank"], r["sku"], r["name"], r["scan_count"]) for r in rows]

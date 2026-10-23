# The published ranking, and the bookkeeping that decides when to rebuild it.
#
# Owns two tables: popularity_state, a single row saying which stretch of the scan stream the current ranking covers, and popularity_snapshot, the ranking itself.
from datetime import datetime
from typing import NamedTuple

import asyncpg


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


class RankingRepo:
    def __init__(self, pool: asyncpg.Pool):
        self._pool = pool

    async def claim_rebuild_if_due(self) -> tuple[int, int] | None:
        """Claims the right to rebuild the ranking, if enough scans have passed. Returns the stretch to count, or None."""
        # Asking how far the log has got, deciding whether that is far enough, and claiming the rebuild all happen in one statement. Two pollers can therefore never both claim the same rebuild, and a poll costs one round trip rather than two.
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

    async def publish(self, conn: asyncpg.Connection, ranked: list[PopularItemRow]) -> None:
        """Replaces the whole ranking with a new one."""
        # Takes a connection because the old ranking is deleted before the new one is written, so a reader must not be able to catch it empty. The caller wraps this and the scan prune in one transaction.
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

    def acquire(self):
        """Hands out a connection so the caller can publish and prune in one transaction."""
        return self._pool.acquire()

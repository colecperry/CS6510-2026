# Tracks which items are being scanned most often right now.
#
# record_scan() runs on every scan. Most calls just log it and return; every
# 500th call also rebuilds the ranking that GET /analytics/popular-items
# serves. Recomputing on a schedule like this keeps the scan path fast.
import app.catalog_cache as catalog_cache
import app.pool as pool_module
from app.config import settings


async def record_scan(sku: str) -> None:
    """Logs one scan, and rebuilds the ranking every slide_interval scans."""
    # Every scan takes a number from one global sequence, which makes "the
    # most recent 1000 scans" a simple range of numbers later on.
    scan_seq = await pool_module.pool.fetchval(
        "INSERT INTO scan_log (sku) VALUES ($1) RETURNING seq", sku
    )

    # This UPDATE only matches when enough scans have passed since the last
    # rebuild. Putting the condition inside the statement means exactly one
    # caller can win it, even with 100 stations scanning at once. The other
    # 499 out of 500 calls get nothing back and return here.
    state_row = await pool_module.pool.fetchrow(
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
    if state_row is None:
        return

    # This call won, so count up the window it just claimed.
    ranked = await pool_module.pool.fetch(
        """
        SELECT sku, count(*) AS scan_count
        FROM scan_log
        WHERE seq > $1 AND seq <= $2
        GROUP BY sku
        ORDER BY scan_count DESC
        LIMIT $3
        """,
        state_row["window_start"],
        state_row["window_end"],
        settings.popularity_snapshot_cap,
    )

    async with pool_module.pool.acquire() as conn:
        async with conn.transaction():
            # Swap the whole ranking in one transaction, so a reader never
            # catches it half-built.
            await conn.execute("DELETE FROM popularity_snapshot")
            for rank, row in enumerate(ranked, start=1):
                name, _ = catalog_cache.catalog[row["sku"]]
                await conn.execute(
                    """
                    INSERT INTO popularity_snapshot (rank, sku, name, scan_count)
                    VALUES ($1, $2, $3, $4)
                    """,
                    rank,
                    row["sku"],
                    name,
                    row["scan_count"],
                )
            # Scans behind the window can never be counted again, so drop
            # them rather than letting scan_log grow forever.
            await conn.execute(
                "DELETE FROM scan_log WHERE seq <= $1", state_row["window_start"]
            )

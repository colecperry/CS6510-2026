# Tracks the hopping window of recent scans for the popular-items feature.
import app.catalog_cache as catalog_cache
import app.pool as pool_module
from app.config import settings


async def record_scan(sku: str) -> None:
    """Logs one scan, and every 500 scans, recomputes the popularity ranking.

    Takes: sku - the item that was just scanned.
    Returns: nothing.
    """
    # Every scan joins the global sequence, regardless of which transaction.
    scan_seq = await pool_module.pool.fetchval(
        "INSERT INTO scan_log (sku) VALUES ($1) RETURNING seq", sku
    )

    # Only recomputes once slide_interval scans have passed since the last recompute - most calls won't match this and just return here.
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

    # This call "won" the recompute - rank the current window's scans.
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
            # Replace the old ranking wholesale with the new one.
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
            # Scans behind the window are unreachable by any future recompute.
            await conn.execute(
                "DELETE FROM scan_log WHERE seq <= $1", state_row["window_start"]
            )

# GET /analytics/popular-items - the current most-scanned ranking.
#
# Only reads. The ranking itself is built by popularity.py as scans come in.
from fastapi import APIRouter

import app.pool as pool_module
from app.models import PopularItem, PopularItemsResponse

router = APIRouter()


@router.get("/analytics/popular-items")
async def get_popular_items(limit: int = 10) -> PopularItemsResponse:
    """Returns the current ranking, plus which scans it was built from."""
    # Which window the ranking covers. Always a single row, id = 1.
    state = await pool_module.pool.fetchrow(
        """
        SELECT window_size, slide_interval, window_start, window_end, computed_at
        FROM popularity_state
        WHERE id = 1
        """
    )

    # Already ranked and trimmed when it was built, so just take the top few.
    rows = await pool_module.pool.fetch(
        "SELECT rank, sku, name, scan_count FROM popularity_snapshot ORDER BY rank LIMIT $1",
        limit,
    )
    items = [
        PopularItem(sku=row["sku"], name=row["name"], scan_count=row["scan_count"], rank=row["rank"])
        for row in rows
    ]

    return PopularItemsResponse(
        window_size=state["window_size"],
        slide_interval=state["slide_interval"],
        window_start=state["window_start"],
        window_end=state["window_end"],
        computed_at=state["computed_at"],
        items=items,
    )

# GET /analytics/popular-items - filled in at Step 20.
from fastapi import APIRouter

import app.pool as pool_module
from app.models import PopularItem, PopularItemsResponse

router = APIRouter()


@router.get("/analytics/popular-items")
async def get_popular_items(limit: int = 10) -> PopularItemsResponse:
    """Returns the current popular-items ranking.

    Takes: limit - how many top items to return (defaults to 10).
    Returns: a PopularItemsResponse with the window metadata and ranked items.
    """
    # Window metadata: single row, always id=1.
    state = await pool_module.pool.fetchrow(
        """
        SELECT window_size, slide_interval, window_start, window_end, computed_at
        FROM popularity_state
        WHERE id = 1
        """
    )

    # Already ranked and capped by Step 19 - just take the top `limit`.
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

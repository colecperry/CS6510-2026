# GET /inventory/low-stock - filled in at Step 18.
from datetime import datetime, timezone

from fastapi import APIRouter

import app.pool as pool_module
from app.config import settings
from app.models import LowStockAlert, LowStockResponse

router = APIRouter()


@router.get("/inventory/low-stock")
async def get_low_stock(threshold: int | None = None) -> LowStockResponse:
    """Lists every item currently below a stock threshold.

    Takes: threshold - optional query param; falls back to the configured default.
    Returns: a LowStockResponse with the threshold used and matching alerts.
    """
    # Query param overrides the configured default if given.
    effective_threshold = threshold if threshold is not None else settings.low_stock_default_threshold

    # Every SKU currently below the threshold, lowest stock first.
    rows = await pool_module.pool.fetch(
        """
        SELECT c.sku, c.name, s.qty
        FROM stock s
        JOIN catalog c ON c.sku = s.sku
        WHERE s.qty < $1
        ORDER BY s.qty ASC
        """,
        effective_threshold,
    )

    # Computed live at request time, like the mock server - no stored history.
    now = datetime.now(timezone.utc)
    alerts = [
        LowStockAlert(
            sku=row["sku"],
            name=row["name"],
            current_stock=row["qty"],
            threshold=effective_threshold,
            triggered_at=now,
        )
        for row in rows
    ]

    return LowStockResponse(threshold=effective_threshold, generated_at=now, alerts=alerts)

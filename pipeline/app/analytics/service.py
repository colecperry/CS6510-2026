# The reporting rules: what counts as popular, and what counts as low stock.
#
# Knows nothing about HTTP and writes no SQL. Reading a ranking happens here; building one happens in pipeline.py, which this hands scans to.
from datetime import datetime, timezone

from app.analytics.pipeline import AnalyticsPipeline
from app.data.analytics_repo import AnalyticsRepo
from app.schemas import (
    LowStockAlert,
    LowStockResponse,
    PopularItem,
    PopularItemsResponse,
)


class AnalyticsService:
    def __init__(
        self,
        analytics: AnalyticsRepo,
        pipeline: AnalyticsPipeline,
        low_stock_threshold: int,
    ):
        self._analytics = analytics                    # reads only, now
        self._pipeline = pipeline                      # where scans go
        self._low_stock_threshold = low_stock_threshold

    def record_scan(self, sku: str) -> None:
        """Hands a scan to the pipeline. Not async: nothing is waited on."""
        # Everything this used to do - the write, the rebuild check, the ranking - now happens in the background instead of here.
        self._pipeline.submit(sku)

    async def popular_items(self, limit: int) -> PopularItemsResponse:
        """Returns the current ranking, plus which scans built it."""
        state = await self._analytics.window_state()
        rows = await self._analytics.top_items(limit)
        return PopularItemsResponse(
            window_size=state.window_size,
            slide_interval=state.slide_interval,
            window_start=state.window_start,
            window_end=state.window_end,
            computed_at=state.computed_at,
            items=[
                PopularItem(sku=r.sku, name=r.name, scan_count=r.scan_count, rank=r.rank)
                for r in rows
            ],
        )

    async def low_stock(self, threshold: int | None) -> LowStockResponse:
        """Lists items running out. Falls back to the configured threshold."""
        effective = threshold if threshold is not None else self._low_stock_threshold
        rows = await self._analytics.below_threshold(effective)

        # Worked out fresh each request rather than stored, so there is no alert history to keep in step with actual stock.
        now = datetime.now(timezone.utc)
        return LowStockResponse(
            threshold=effective,
            generated_at=now,
            alerts=[
                LowStockAlert(
                    sku=r.sku,
                    name=r.name,
                    current_stock=r.qty,
                    threshold=effective,
                    triggered_at=now,
                )
                for r in rows
            ],
        )

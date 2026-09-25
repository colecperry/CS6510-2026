# The reporting rules: what counts as popular, and what counts as low stock.
#
# Knows nothing about HTTP and writes no SQL. The API layer calls these
# methods; the transactions layer calls record_scan() on every scan.
from datetime import datetime, timezone

from app.data.analytics_repo import AnalyticsRepo, PopularItemRow
from app.data.catalog_repo import CatalogRepo
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
        catalog: CatalogRepo,
        low_stock_threshold: int,
        snapshot_cap: int,
    ):
        self._analytics = analytics
        self._catalog = catalog
        self._low_stock_threshold = low_stock_threshold
        self._snapshot_cap = snapshot_cap

    async def record_scan(self, sku: str) -> None:
        """Logs a scan, and rebuilds the ranking when enough have piled up."""
        seq = await self._analytics.log_scan(sku)

        # Rebuilding on every scan would be wasteful, so it happens once
        # every slide_interval scans. This call returns None for the other
        # 499 out of 500, which is the normal, cheap path.
        window = await self._analytics.claim_recompute(seq)
        if window is None:
            return

        start, end = window
        counts = await self._analytics.count_window(start, end, self._snapshot_cap)

        # Item names come from the in-memory catalog, not another query.
        ranked = [
            PopularItemRow(
                rank=position,
                sku=count.sku,
                name=self._catalog.get(count.sku).name,
                scan_count=count.scan_count,
            )
            for position, count in enumerate(counts, start=1)
        ]
        await self._analytics.publish_ranking(ranked, prune_before=start)

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

        # Worked out fresh each request rather than stored, so there is no
        # alert history to keep in step with actual stock.
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

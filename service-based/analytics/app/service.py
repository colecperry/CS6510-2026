# Which items are being scanned most often.
#
# Only reads here. The ranking itself is built in the background by rebuilder.py, from scans the checkout service wrote to the shared database.
from common.data.scans_repo import ScansRepo
from common.schemas import PopularItem, PopularItemsResponse


class AnalyticsService:
    def __init__(self, scans: ScansRepo):
        self._scans = scans

    async def popular_items(self, limit: int) -> PopularItemsResponse:
        """Returns the current ranking, plus which stretch of scans built it."""
        # Two cheap reads of already-computed results. The counting happened earlier, in the background, so a caller never waits for it.
        state = await self._scans.window_state()
        rows = await self._scans.top_items(limit)
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

# Rebuilds the most-scanned ranking in the background.
#
# Nobody tells this service when to rebuild, because checkout only posts scans and then forgets about them. So it asks its own database on a timer: has the scan log moved far enough for a new ranking, and may I be the one to build it?
#
# In week 4 this was two filters fed by an in-process queue. A queue cannot cross a process boundary, so the trigger became a poll.
import asyncio
import logging

from app.data.catalog_repo import CatalogRepo
from app.data.ranking_repo import PopularItemRow, RankingRepo
from app.data.scans_repo import ScansRepo

logger = logging.getLogger(__name__)

# How often to ask. A rebuild is due every 500 scans, which at full load is roughly every 400ms, so asking four times a second keeps the ranking about as fresh as the previous weeks. Each ask is one cheap statement.
POLL_SECONDS = 0.25


class RankingRebuilder:
    def __init__(
        self,
        scans: ScansRepo,
        ranking: RankingRepo,
        catalog: CatalogRepo,
        snapshot_cap: int,
    ):
        self._scans = scans                  # the scan stream
        self._ranking = ranking              # the published ranking and its window
        self._catalog = catalog              # item names, from memory
        self._snapshot_cap = snapshot_cap    # how many items the ranking keeps
        self._task: asyncio.Task | None = None

    async def poll_once(self) -> None:
        """Asks whether a rebuild is due, and does it if so."""
        # function from last slide - returns None if not due, or the window to count if due
        window = await self._ranking.claim_rebuild_if_due()
        if window is None:
            return   # not due. the usual answer.

        start, end = window
        # count how many times each item was scaned, return top N items
        counts = await self._scans.count_window(start, end, self._snapshot_cap)

        # Names come from this service's own copy of the catalogue, never from a call to inventory.
        ranked = [
            PopularItemRow(
                rank=position,
                sku=count.sku,
                name=self._catalog.name_for(count.sku),
                scan_count=count.scan_count,
            )
            for position, count in enumerate(counts, start=1)
        ]

        # One transaction: if saving the ranking fails, the old scans are not deleted either.
        async with self._ranking.acquire() as conn:
            async with conn.transaction():
                await self._ranking.publish(conn, ranked) # replace old ranking with new one
                await self._scans.prune_before(conn, start) # delete scans older than this window

    async def start(self) -> None:
        """Starts polling. Call once, at startup."""
        self._task = asyncio.create_task(self._keep_polling())

    async def stop(self) -> None:
        """Stops polling. Call at shutdown."""
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)

    async def _keep_polling(self) -> None:
        """Polls forever, and survives an error rather than dying quietly."""
        while True:
            try:
                await self.poll_once()
            except Exception:
                # Without this, one bad rebuild would stop the ranking updating for the life of the process, with nothing in the logs to say why.
                logger.exception("ranking rebuild failed, will try again")
            await asyncio.sleep(POLL_SECONDS)

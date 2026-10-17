# Rebuilds the most-scanned ranking in the background.
#
# Nobody tells this service that a scan happened, because the checkout service is not allowed to call it. So it asks the database on a timer instead: has the scan log moved far enough for a new ranking, and may I be the one to build it?
#
# In the previous week this was two filters fed by a queue in the same process. The queue could not cross a process boundary, so the trigger became a poll and the database became the hand-off.
import asyncio
import logging

from common.data.scans_repo import PopularItemRow

logger = logging.getLogger(__name__)

# How often to ask. A rebuild is due every 500 scans, which at full load is roughly every 400ms, so asking four times a second keeps the ranking about as fresh as the previous weeks. Each ask is one cheap statement.
POLL_SECONDS = 0.25


class RankingRebuilder:
    def __init__(self, scans, catalog, snapshot_cap: int):
        self._scans = scans
        self._catalog = catalog              # item names, from memory
        self._snapshot_cap = snapshot_cap    # how many items the ranking keeps
        self._task: asyncio.Task | None = None

    async def poll_once(self) -> None:
        """Asks whether a rebuild is due, and does it if so."""
        window = await self._scans.claim_rebuild_if_due()
        if window is None:
            return                           # not due. the usual answer.

        start, end = window
        counts = await self._scans.count_window(start, end, self._snapshot_cap)

        # Names come from this service's own copy of the catalogue, not another query and certainly not another service.
        ranked = [
            PopularItemRow(
                rank=position,
                sku=count.sku,
                name=self._catalog.get(count.sku).name,
                scan_count=count.scan_count,
            )
            for position, count in enumerate(counts, start=1)
        ]

        # Publish, and drop scans too old for any future window to include.
        await self._scans.publish_ranking(ranked, prune_before=start)

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

# The windowed analytics, built as a pipeline.
#
# A pipeline is a chain of small workers ("filters"), each doing one job and
# passing its result to the next through a queue ("pipe"). No filter calls
# another.
#
#   scan -> [ingest] -> [window] -> [ranker] -> published ranking
#
# Recording a scan used to happen while the customer waited. Now it is
# dropped in a queue and the filters do the work in the background.
import asyncio
import logging

from app.data.analytics_repo import AnalyticsRepo, PopularItemRow
from app.data.catalog_repo import CatalogRepo

logger = logging.getLogger(__name__)

# Most scans one database write may carry. The ranking rebuilds every 500
# scans, so staying under that keeps a batch from skipping a rebuild.
MAX_BATCH = 200

# Most scans allowed to pile up unwritten, so a stalled pipeline cannot eat
# all the memory.
MAX_PENDING = 50_000


class AnalyticsPipeline:
    def __init__(
        self,
        analytics: AnalyticsRepo,
        catalog: CatalogRepo,
        snapshot_cap: int,
    ):
        self._analytics = analytics          # the only thing here that runs SQL
        self._catalog = catalog              # in-memory item names, no queries
        self._snapshot_cap = snapshot_cap    # how many items the ranking keeps

        # The pipes. Only the first is capped: it is the one fed from outside
        # by customers, so it is the only one that could run away.
        self._scans: asyncio.Queue[str] = asyncio.Queue(maxsize=MAX_PENDING)
        self._sequences: asyncio.Queue[int] = asyncio.Queue()
        self._windows: asyncio.Queue[tuple[int, int]] = asyncio.Queue()

        self._tasks: list[asyncio.Task] = []  # the running filters
        self.dropped = 0                      # scans lost to a full queue

    def submit(self, sku: str) -> None:
        """Hands one scan to the pipeline. Never waits and never touches the database."""
        # Why scanning got faster: the request ends here, not at the database.
        try:
            self._scans.put_nowait(sku)       # drop it in the first pipe
        except asyncio.QueueFull:
            # A lost scan costs the ranking accuracy; a blocked one costs a
            # customer their checkout.
            self.dropped += 1

    # ---- the filters ----
    #
    # Each is a forever-loop: take from the pipe behind, do one job, put the
    # result in the pipe in front. Awaiting an empty queue just sleeps.

    async def _ingest(self) -> None:
        """Filter 1: writes waiting scans to the database, in batches."""
        while True:
            batch = [await self._scans.get()]        # sleep until a scan arrives

            # This get takes only what is already there, so the batch is
            # however busy the shop is. No timer, nothing to tune.
            while len(batch) < MAX_BATCH:
                try:
                    batch.append(self._scans.get_nowait())   # grab what is there
                except asyncio.QueueEmpty:
                    break                                     # nothing left, go write

            latest_seq = await self._analytics.log_scans(batch)  # one write, any size
            await self._sequences.put(latest_seq)                # hand downstream
            for _ in batch:
                self._scans.task_done()                          # this batch is done

    async def _window(self) -> None:
        """Filter 2: decides when a ranking is due, and which scans it covers."""
        while True:
            latest_seq = await self._sequences.get()   # how far the stream has got

            # Ask whether enough scans have happened for a new ranking. The
            # answer is the stretch of scans to count, or nothing yet.
            window = await self._analytics.claim_recompute(latest_seq)
            if window is not None:                     # None most of the time
                await self._windows.put(window)        # due: hand downstream
            self._sequences.task_done()

    async def _rank(self) -> None:
        """Filter 3: counts one window and publishes the new ranking."""
        while True:
            start, end = await self._windows.get()     # a stretch of scans to count

            counts = await self._analytics.count_window(start, end, self._snapshot_cap)

            # Names come from memory. Safe because unknown items are rejected
            # long before they reach here.
            ranked = [
                PopularItemRow(
                    rank=position,
                    sku=count.sku,
                    name=self._catalog.get(count.sku).name,
                    scan_count=count.scan_count,
                )
                for position, count in enumerate(counts, start=1)
            ]

            # End of the line: publish, and drop scans too old to count again.
            await self._analytics.publish_ranking(ranked, prune_before=start)
            self._windows.task_done()

    # ---- running the filters ----

    async def start(self) -> None:
        """Starts the three filters running in the background. Call once, at startup."""
        self._tasks = [
            asyncio.create_task(self._keep_running(self._ingest, "ingest")),
            asyncio.create_task(self._keep_running(self._window, "window")),
            asyncio.create_task(self._keep_running(self._rank, "ranker")),
        ]

    async def stop(self) -> None:
        """Lets the pipeline finish what it is holding, then stops it."""
        # Scans still in a pipe have not reached the database yet, so give
        # them a moment rather than dropping them on the floor.
        try:
            await asyncio.wait_for(self._drain(), timeout=5)
        except TimeoutError:
            logger.warning("analytics pipeline still busy at shutdown, stopping anyway")

        for task in self._tasks:
            task.cancel()                                 # ask each filter to stop
        await asyncio.gather(*self._tasks, return_exceptions=True)  # wait for them

        if self.dropped:
            logger.warning("analytics pipeline dropped %d scans", self.dropped)

    async def _drain(self) -> None:
        """Waits until every pipe is empty and its work actually finished."""
        # Waiting for a queue to look empty is not enough: an item can be out
        # of it and still being worked on. join() waits for the filter to say
        # it is done.
        await self._scans.join()       # every scan written
        await self._sequences.join()   # every window decision made
        await self._windows.join()     # every ranking published

    async def _keep_running(self, filter_fn, name: str) -> None:
        """Restarts a filter if it errors, so one bad batch cannot stop it forever."""
        while True:
            try:
                await filter_fn()
            except Exception:
                # Catches real errors only. A filter being stopped on purpose
                # is not caught here, so stop() still works.
                logger.exception("analytics pipeline: %s filter failed, restarting", name)

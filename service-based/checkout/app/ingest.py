# Carries scans out of the request and into the shared scan log.
#
# The reporting service is not allowed to be told a scan happened, so checkout writes scans where reporting can find them. That write must not happen while a shopper waits, so scans go into a queue and a background task writes them in batches.
#
# In the previous week this was the first of three filters in one process. The other two moved to the reporting service, and the database is now the hand-off between them.
import asyncio
import logging

logger = logging.getLogger(__name__)

# Most scans one database write may carry. The ranking rebuilds every 500 scans, so staying under that keeps one batch from skipping a rebuild point.
MAX_BATCH = 200

# Most scans allowed to pile up unwritten, so a stalled writer cannot eat all the memory.
MAX_PENDING = 50_000


class ScanWriter:
    def __init__(self, scans):
        self._scans = scans                                   # the shared scan log
        self._queue: asyncio.Queue[str] = asyncio.Queue(maxsize=MAX_PENDING)
        self._task: asyncio.Task | None = None
        self.dropped = 0                                      # scans lost to a full queue

    def record(self, sku: str) -> None:
        """Hands one scan over. Never waits, and never touches the database."""
        # Why scanning stayed fast after the split: the shopper's request ends here.
        try:
            self._queue.put_nowait(sku)
        except asyncio.QueueFull:
            # A lost scan costs the ranking accuracy; a blocked one costs a customer their checkout.
            self.dropped += 1

    async def start(self) -> None:
        """Starts the background writer. Call once, at startup."""
        self._task = asyncio.create_task(self._keep_running())

    async def stop(self) -> None:
        """Writes whatever is still queued, then stops. Call at shutdown."""
        # Queued scans have not reached the database yet, so give them a moment rather than dropping them on the floor.
        try:
            await asyncio.wait_for(self._queue.join(), timeout=5)
        except TimeoutError:
            logger.warning("scan writer still busy at shutdown, stopping anyway")

        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)

        if self.dropped:
            logger.warning("scan writer dropped %d scans", self.dropped)

    async def _write_batches(self) -> None:
        """Takes everything waiting and writes it, forever."""
        while True:
            batch = [await self._queue.get()]        # sleep until a scan arrives

            # This get takes only what is already there, so the batch is however busy the shop is. No timer, nothing to tune.
            while len(batch) < MAX_BATCH:
                try:
                    batch.append(self._queue.get_nowait())
                except asyncio.QueueEmpty:
                    break

            await self._scans.log_scans(batch)       # one write, any size
            for _ in batch:
                self._queue.task_done()              # only now is the batch finished

    async def _keep_running(self) -> None:
        """Restarts the writer if it errors, so one bad batch cannot stop it forever."""
        while True:
            try:
                await self._write_batches()
            except Exception:
                # Catches real errors only. Being stopped on purpose is not caught here, so stop() still works.
                logger.exception("scan writer failed, restarting")

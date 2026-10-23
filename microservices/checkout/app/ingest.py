# Carries scans out of the request and across to the analytics service.
#
# Analytics owns the scan log and this service cannot write to it, so a scan has to travel as a network call. That call must not happen while a shopper waits, so scans go into a queue and a background task posts them in batches.
#
# Week 5 wrote the same batches into a shared database table. Only the destination changed, which is what keeps this week's scan latency comparable to last week's.
import asyncio
import logging

logger = logging.getLogger(__name__)

# Most scans one batch may carry. The ranking rebuilds every 500 scans, so staying under that keeps one batch from skipping a rebuild point.
MAX_BATCH = 200

# Most scans allowed to pile up undelivered, so a stalled writer cannot eat all the memory.
MAX_PENDING = 50_000


class ScanWriter:
    def __init__(self, analytics):
        self._analytics = analytics                           # the service that owns the scan log
        self._queue: asyncio.Queue[str] = asyncio.Queue(maxsize=MAX_PENDING)
        self._task: asyncio.Task | None = None
        self.dropped = 0                                      # scans lost to a full queue
        self.undelivered = 0                                  # scans lost to analytics being down

    def record(self, sku: str) -> None:
        """Hands one scan over. Never waits, and never touches the network."""
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
        """Delivers whatever is still queued, then stops. Call at shutdown."""
        # Queued scans have not left this process yet, so give them a moment rather than dropping them on the floor.
        try:
            await asyncio.wait_for(self._queue.join(), timeout=5)
        except TimeoutError:
            logger.warning("scan writer still busy at shutdown, stopping anyway")

        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)

        if self.dropped or self.undelivered:
            logger.warning(
                "scan writer lost %d scans to a full queue and %d to analytics being down",
                self.dropped,
                self.undelivered,
            )

    async def _send_batches(self) -> None:
        """Takes everything waiting and posts it, forever."""
        while True:
            batch = [await self._queue.get()]        # sleep until a scan arrives

            # This get takes only what is already there, so the batch is however busy the shop is. No timer, nothing to tune.
            while len(batch) < MAX_BATCH:
                try:
                    batch.append(self._queue.get_nowait())
                except asyncio.QueueEmpty:
                    break

            if not await self._analytics.send_scans(batch):
                # Counted rather than retried. Holding the batch would back the queue up behind a service that may be down for a while, and then real scans would start being dropped instead.
                self.undelivered += len(batch)

            for _ in batch:
                self._queue.task_done()              # only now is the batch finished

    async def _keep_running(self) -> None:
        """Restarts the writer if it errors, so one bad batch cannot stop it forever."""
        while True:
            try:
                await self._send_batches()
            except Exception:
                # Catches real errors only. Being stopped on purpose is not caught here, so stop() still works.
                logger.exception("scan writer failed, restarting")

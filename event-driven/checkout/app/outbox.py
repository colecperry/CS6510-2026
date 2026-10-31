# Sends the messages the outbox table says this service still owes, and nothing else.
#
# A sale is written into the outbox inside the same transaction that marks the basket paid, so by the time this runs the sale is already a fact. All that is left is delivery, which nobody is waiting on.
# This is the piece that turns "the database has it" into "the broker has it", and it is the only place in checkout that can be slow without a shopper noticing.
import asyncio
import logging

from app.bus.publisher import CheckoutPublisher
from app.data.outbox_repo import OutboxRepo

logger = logging.getLogger(__name__)

# How many owed messages to take in one pass. Large enough to catch up quickly after an outage, small enough that one pass cannot hold the loop for long.
BATCH = 100

# How long to wait after a failed pass before trying again. The broker being down is the normal reason, and retrying every few milliseconds would fill the log without making it come back any sooner.
BACKOFF_SECONDS = 2.0


class OutboxRelay:
    def __init__(
        self, outbox: OutboxRepo, publisher: CheckoutPublisher, poll_ms: int
    ) -> None:
        self._outbox = outbox
        self._publisher = publisher
        self._poll = poll_ms / 1000
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        """Starts the relay in the background. Call once, at startup."""
        self._task = asyncio.create_task(self._keep_running())

    async def stop(self) -> None:
        """Sends whatever it can, then stops. Call at shutdown."""
        # One last pass, so an ordinary restart does not leave sales sitting in the table waiting for the next start. Anything that still fails stays unsent, which is exactly what the table is for.
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
        try:
            await self._drain_once()
        except Exception:
            logger.warning("could not drain the outbox at shutdown, leaving it for the next start")

    async def _drain_once(self) -> int:
        """Sends every message currently owed, and returns how many went. Raises if the broker refuses one."""
        sent = 0
        while True:
            rows = await self._outbox.unsent(BATCH)
            if not rows:
                return sent
            for row in rows:
                # The order matters and is the reason the outbox works. The broker confirms it has the message, and only then is the row marked sent. Reversing these two would turn a broker failure into a sale nobody is ever told about.
                await self._publisher.send_committed(
                    row.message_id, row.routing_key, row.payload
                )
                await self._outbox.mark_sent(row.message_id)
                sent += 1

    async def _keep_running(self) -> None:
        """Polls for owed messages forever, surviving anything that goes wrong."""
        # Catching everything is deliberate. A relay that dies quietly looks exactly like a working system until someone notices stock has stopped moving, and the only cost of staying alive is a log line.
        while True:
            try:
                await self._drain_once()
                await asyncio.sleep(self._poll)
            except Exception:
                logger.warning("outbox relay could not send, retrying shortly", exc_info=True)
                await asyncio.sleep(BACKOFF_SECONDS)

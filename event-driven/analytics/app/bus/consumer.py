# Everything this service listens for, and what it does when it hears it.
#
# Analytics subscribes and never publishes. Scans arrive as batches it had no part in producing, and the ranking it builds from them is its own business.
# Replaces last week's /internal/scans route, which was the same batch arriving as an HTTP post that checkout had to address to this service by name.
import logging

from app.data.scans_repo import ScansRepo
from common.bus import Bus

logger = logging.getLogger(__name__)

# The queue this service owns. Durable, so scans published while analytics is stopped are waiting for it when it comes back rather than being lost.
QUEUE = "analytics.scans"

# The label analytics cares about. Checkout chose the name when it published; analytics subscribes to it without either service naming the other.
SCANS_KEY = "items.scanned"

# How many batches the broker may hand this service before it acknowledges any. Each batch is up to 200 scans, so this is already thousands of rows in flight, and a larger number would only mean one consumer sitting on work a second one could do.
PREFETCH = 20


class ScansConsumer:
    def __init__(self, bus: Bus, scans: ScansRepo) -> None:
        self._bus = bus
        self._scans = scans

    async def start(self) -> None:
        """Declares the queue and begins recording scans. Call once, at startup."""
        await self._bus.consume(QUEUE, SCANS_KEY, self._handle)

    async def _handle(self, message: dict) -> None:
        """Records one batch of scans."""
        # The acknowledgement is sent by common/bus.py only once this returns, so a crash part way through replays the batch rather than losing it.
        # Scans are counted, not keyed, so a replayed batch would slightly overcount the ranking and nothing else. The ranking is the one thing allowed to be approximate, and keying every scan would cost more than the error it prevents.
        skus = message.get("skus") or []
        if skus:
            await self._scans.record_scans(skus)

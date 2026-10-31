# Everything this service announces to the rest of the system.
#
# Checkout publishes and never subscribes: it is the only service that knows a sale or a scan happened, and no other service tells it anything. The message names live here rather than in common/, so the shape of what checkout says belongs to checkout.
#
# Replaces last week's two HTTP clients. Where those held another service's address and waited for its answer, this hands a message to the broker and stops.
import logging
from uuid import UUID, uuid4

from common.bus import Bus

logger = logging.getLogger(__name__)

# A shopper scanned some items. Transient on purpose: analytics may lag, and a dropped batch costs a ranking a little accuracy rather than costing money.
SCANS_KEY = "items.scanned"

# A shopper paid. Persistent and confirmed, because this is the message that takes stock off the shelf and nothing else will.
SALE_KEY = "sale.completed"


class CheckoutPublisher:
    def __init__(self, bus: Bus) -> None:
        self._bus = bus

    async def send_scans(self, skus: list[str]) -> bool:
        """Announces a batch of scans. Returns False if the broker could not take it, which the caller treats as a lost batch rather than an error."""
        # Never raises. A reporting message must not be able to interrupt the loop that is draining the scan queue, because a stalled drain is what starts costing real scans.
        try:
            await self._bus.publish(
                SCANS_KEY,
                {"messageId": str(uuid4()), "skus": skus},
                persistent=False,
            )
            return True
        except Exception:
            logger.warning("could not publish %d scans, dropping them", len(skus))
            return False

    async def send_committed(self, message_id: UUID, routing_key: str, payload: dict) -> None:
        """Sends one message the service has already committed to sending. Returns only once the broker has it on disk, and raises if it does not."""
        # Raises deliberately, unlike send_scans. The caller is the outbox relay, whose whole job is to leave the row unsent and try again, so a swallowed failure here would be a sale nobody is ever told about.
        # The id comes from the caller rather than being generated here: it is the outbox row's own key, which is what makes a copy the broker delivers twice recognisable as the same sale rather than a second one.
        await self._bus.publish(
            routing_key,
            {"messageId": str(message_id), **payload},
            persistent=True,
        )

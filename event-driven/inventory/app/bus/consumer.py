# Everything this service listens for, and what it does when it hears it.
#
# Inventory subscribes and never publishes. It is told that a sale happened and decides for itself that stock must come down: checkout never asks it to, and would not know if it refused.
# Replaces last week's /internal/stock/reduce route. Where that was a request checkout waited on, this is an announcement that was already true before it arrived.
import logging
from uuid import UUID

from app.stock_mover import StockMover
from common.bus import Bus

logger = logging.getLogger(__name__)

# The queue this service owns. It is declared by inventory, not by checkout, and exists whether or not inventory is running, which is what makes a sale survive this service being down.
QUEUE = "inventory.sales"

# The label inventory cares about. Checkout chose the name when it published; inventory subscribes to it without either service naming the other.
SALE_KEY = "sale.completed"

# How many sales the broker may hand this service before it acknowledges any. A sale is one short transaction, so a small number keeps the work shareable with a second copy of the service without ever leaving this one idle.
PREFETCH = 50


class SalesConsumer:
    def __init__(self, bus: Bus, stock: StockMover) -> None:
        self._bus = bus
        self._stock = stock

    async def start(self) -> None:
        """Declares the queue and begins handling sales. Call once, at startup."""
        await self._bus.consume(QUEUE, SALE_KEY, self._handle)

    async def _handle(self, message: dict) -> None:
        """Takes stock off the shelf for one sale."""
        # The acknowledgement is sent by common/bus.py only once this returns without raising, and reduce() does the database work in one transaction, so the message is still on the queue until the stock has actually moved.
        message_id = UUID(message["messageId"])
        lines = [(line["sku"], int(line["qty"])) for line in message["lines"]]

        applied = await self._stock.reduce(message_id, lines)
        if not applied:
            # A redelivered copy of a sale already applied. Expected rather than exceptional: the broker promises at least one delivery, not exactly one, so recognising the repeat is this service's job.
            logger.debug("sale %s already applied, ignoring the repeat", message_id)

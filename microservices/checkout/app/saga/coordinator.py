# Runs a payment across two databases.
#
# Paying changes this service's basket and inventory's stock. Those live in different databases, so no single transaction can cover both. Instead the steps run in order, each one recorded, so an interrupted payment can be finished or undone rather than guessed at.
#
# Stock goes first on purpose. Crashing after it leaves the shop believing it has less than it does, which cannot oversell. The other order would leave a basket claiming it was paid for with the stock still on the shelf.
import logging
import uuid

from app.clients.inventory_client import InventoryUnavailable
from common.errors import ConflictError, NotFoundError

logger = logging.getLogger(__name__)


class PaymentCoordinator:
    def __init__(self, baskets, sagas, inventory):
        self._baskets = baskets          # this service's baskets and receipt lines
        self._sagas = sagas              # the record of how far a payment got
        self._inventory = inventory      # the service that owns stock

    async def pay(self, transaction_id: str):
        """Pays for a basket and returns the rows a receipt is built from. Raises if it cannot."""
        saga_id = f"saga-{uuid.uuid4().hex}"

        # Claiming the basket and writing down what we intend to do happen together, so a payment can never be in flight without a record saying so.
        async with self._sagas.acquire() as conn:
            async with conn.transaction():
                if not await self._sagas.claim_basket(conn, transaction_id):
                    raise await self._refusal(transaction_id)
                lines = await self._baskets.receipt_lines(conn, transaction_id)
                await self._sagas.open_saga(
                    conn,
                    saga_id,
                    transaction_id,
                    [(line.sku, line.quantity) for line in lines],
                )

        # Step one, in the other service's database. The client retries inside its budget before giving up.
        try:
            await self._inventory.reduce_stock(
                saga_id, [(line.sku, line.quantity) for line in lines]
            )
        except InventoryUnavailable:
            # The reduction may or may not have landed: a lost reply looks exactly like a lost request. Asking inventory to undo this key covers both, because undoing something never applied changes nothing.
            await self._try_compensate(transaction_id, saga_id)
            raise ConflictError(
                "TRANSACTION_NOT_OPEN", "Could not complete payment, please try again"
            )
        await self._sagas.mark(saga_id, "APPLIED")

        # Step two, in this one. From here the books balance again.
        async with self._sagas.acquire() as conn:
            async with conn.transaction():
                row = await self._sagas.finish(conn, transaction_id, saga_id)

        if row is None:
            # The basket stopped being claimable between the two steps, which should not happen while paying_since is held. Stock is already down, so it has to go back.
            await self._try_compensate(transaction_id, saga_id)
            raise ConflictError("TRANSACTION_NOT_OPEN", "Transaction already finalized")

        return row, lines

    async def _try_compensate(self, transaction_id: str, saga_id: str) -> None:
        """Undoes whatever this payment managed to do, and gives the basket back to the shopper."""
        # Stock is repaired before the basket is released, so the books are never right-looking while still wrong.
        try:
            await self._inventory.restore_stock(saga_id)
            await self._sagas.release_basket(transaction_id, saga_id)
        except InventoryUnavailable:
            # Inventory is still unreachable, so this cannot be put right now. The saga row stays unfinished, which is exactly what recovery looks for at the next startup. Swallowed rather than raised, so the shopper still gets a clean refusal instead of a crash.
            logger.warning("could not compensate %s, left for recovery", saga_id)

    async def _refusal(self, transaction_id: str) -> Exception:
        """Works out why a basket could not be claimed, and builds the error the shopper should see."""
        # Only runs after a claim already failed, so a successful payment never pays for this extra query.
        state = await self._baskets.state_of(transaction_id)
        if state is None:
            return NotFoundError("NOT_FOUND", f"No such transaction: {transaction_id}")
        if state.item_count == 0:
            return ConflictError("EMPTY_BASKET", "Cannot complete an empty transaction")
        return ConflictError("TRANSACTION_NOT_OPEN", "Transaction already finalized")

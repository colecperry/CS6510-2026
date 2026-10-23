# Repairs payments that were interrupted, when the process restarts.
#
# Compensation inside the coordinator handles a step that fails and returns. It cannot help when this service dies part way through a payment, because the code that would have compensated no longer exists.
#
# What survives is the saga row. This reads the ones left unfinished, asks inventory whether each actually moved any stock, and drives each to one of the two valid endings: the payment completes, or everything it did is undone.
import logging

from app.clients.inventory_client import InventoryUnavailable

logger = logging.getLogger(__name__)


class SagaRecovery:
    def __init__(self, baskets, sagas, inventory):
        self._baskets = baskets
        self._sagas = sagas
        self._inventory = inventory

    async def recover_once(self) -> dict[str, int]:
        """Finishes or undoes every payment left part way. Returns a count of each outcome."""
        # Runs at startup, before this service accepts traffic. If it served requests first, a shopper could scan into a basket whose stock had already been taken.
        outcome = {"completed": 0, "compensated": 0, "failed": 0}

        for saga in await self._sagas.unfinished():
            try:
                await self._repair(saga)
                # A repaired saga is recorded by which way it went, so the run can report how often this happened.
                outcome["completed" if await self._is_done(saga) else "compensated"] += 1
            except InventoryUnavailable:
                # Inventory is down right now. The row stays unfinished and the next startup tries again, which is better than guessing.
                logger.warning("cannot reach inventory to repair %s, leaving it", saga.saga_id)
                outcome["failed"] += 1

        if any(outcome.values()):
            logger.info("recovered interrupted payments: %s", outcome)
        return outcome

    async def _repair(self, saga) -> None:
        """Drives one interrupted payment to an end state."""
        # Only inventory knows whether the stock actually moved, because a crash can happen either side of its reply.
        applied = await self._inventory.was_applied(saga.saga_id)

        if not applied:
            # Nothing was taken, so there is nothing to undo. Give the basket back.
            await self._sagas.release_basket(saga.transaction_id, saga.saga_id)
            return

        # Stock is down. Finishing the sale is what makes the books balance, so that is preferred over putting it back.
        async with self._sagas.acquire() as conn:
            async with conn.transaction():
                row = await self._sagas.finish(conn, saga.transaction_id, saga.saga_id)

        if row is None:
            # The basket can no longer be completed, so the only way to balance the books is to put the stock back.
            await self._inventory.restore_stock(saga.saga_id)
            await self._sagas.release_basket(saga.transaction_id, saga.saga_id)

    async def _is_done(self, saga) -> bool:
        """Whether the repair ended in a completed sale rather than an undo."""
        return await self._inventory.was_applied(saga.saga_id)

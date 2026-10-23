# Stand-ins for the database and the inventory service, so the payment sequence can be tested without either.
#
# The coordinator and recovery are about the order steps happen in and what happens when one fails. Both are easier to drive from here than from a real database, and these record what was called so a test can assert on the sequence.
from contextlib import asynccontextmanager

from app.clients.inventory_client import InventoryUnavailable
from app.data.baskets_repo import TransactionState


class FakeBaskets:
    def __init__(self, lines=None, state=None):
        self._lines = lines or []
        self._state = state

    async def receipt_lines(self, conn, transaction_id):
        return self._lines

    async def state_of(self, transaction_id):
        return self._state


class FakeSagas:
    """Records the order things happened in, which is what the coordinator is responsible for."""

    def __init__(self, claimable=True, finishable=True, unfinished=None):
        self.claimable = claimable
        self.finishable = finishable
        self.events: list[str] = []
        self.opened: list[tuple] = []
        self.states: dict[str, str] = {}
        self._unfinished = unfinished or []

    # The coordinator claims the basket and opens the saga inside one transaction, so the fake has to offer that shape even though nothing here is transactional.
    @asynccontextmanager
    async def _conn(self):
        yield self

    def acquire(self):
        return self._conn()

    @asynccontextmanager
    async def transaction(self):
        yield

    async def claim_basket(self, conn, transaction_id):
        self.events.append("claim")
        return self.claimable

    async def open_saga(self, conn, saga_id, transaction_id, lines):
        self.events.append("open_saga")
        self.opened.append((saga_id, lines))

    async def mark(self, saga_id, state):
        self.events.append(f"mark:{state}")
        self.states[saga_id] = state

    async def finish(self, conn, transaction_id, saga_id):
        self.events.append("finish")
        if not self.finishable:
            return None
        self.states[saga_id] = "DONE"
        return {
            "station_id": "lane-1",
            "item_count": 2,
            "running_total": 1.70,
            "started_at": "t0",
            "completed_at": "t1",
        }

    async def release_basket(self, transaction_id, saga_id):
        self.events.append("release")
        self.states[saga_id] = "COMPENSATED"

    async def unfinished(self):
        return self._unfinished


class FakeInventory:
    """Stands in for the other service, and can be told to fail either call."""

    def __init__(self, reduce_fails=False, restore_fails=False, applied=False, unreachable=False):
        self._reduce_fails = reduce_fails or unreachable
        self._restore_fails = restore_fails or unreachable
        self._unreachable = unreachable
        self._applied = applied
        self.reduced: list[tuple] = []
        self.restored: list[str] = []

    async def reduce_stock(self, key, lines):
        if self._reduce_fails:
            raise InventoryUnavailable("inventory is down")
        self.reduced.append((key, lines))

    async def restore_stock(self, key):
        if self._restore_fails:
            raise InventoryUnavailable("inventory is down")
        self.restored.append(key)

    async def was_applied(self, key):
        if self._unreachable:
            raise InventoryUnavailable("inventory is down")
        return self._applied

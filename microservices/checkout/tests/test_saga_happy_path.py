# Paying for a basket when every step succeeds, tested without a database or a network.
#
# The coordinator's job is the order of the steps: claim the basket, reduce stock in the other service, then mark the basket paid. These check that order holds and that a basket which cannot be claimed is refused with the right error.
from contextlib import asynccontextmanager

import pytest
from app.data.baskets_repo import ReceiptLineRow, TransactionState
from app.saga.coordinator import PaymentCoordinator
from common.errors import ConflictError, NotFoundError

LINES = [ReceiptLineRow("SKU-1", "Item 1", 0.85, 3)]


class FakeBaskets:
    def __init__(self, state):
        self._state = state

    async def receipt_lines(self, conn, transaction_id):
        return LINES

    async def state_of(self, transaction_id):
        return self._state


class FakeSagas:
    """Records the order things happened in, which is the whole point of the coordinator."""

    def __init__(self, claimable=True):
        self._claimable = claimable
        self.events: list[str] = []
        self.opened: list[tuple] = []

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
        return self._claimable

    async def open_saga(self, conn, saga_id, transaction_id, lines):
        self.events.append("open_saga")
        self.opened.append((saga_id, lines))

    async def mark(self, saga_id, state):
        self.events.append(f"mark:{state}")

    async def finish(self, conn, transaction_id, saga_id):
        self.events.append("finish")
        return {
            "station_id": "lane-1",
            "item_count": 3,
            "running_total": 2.55,
            "started_at": "t0",
            "completed_at": "t1",
        }

    async def release_basket(self, transaction_id, saga_id):
        self.events.append("release")


class FakeInventory:
    def __init__(self):
        self.calls: list[tuple] = []

    async def reduce_stock(self, key, lines):
        self.calls.append((key, lines))


def build(claimable=True, state=None):
    baskets, sagas, inventory = FakeBaskets(state), FakeSagas(claimable), FakeInventory()
    return PaymentCoordinator(baskets, sagas, inventory), sagas, inventory


async def test_stock_is_reduced_before_the_basket_is_marked_paid():
    # The order matters. Crashing after the stock call leaves the shop believing it has less than it does, which cannot oversell. The other order would hand out a receipt for stock still on the shelf.
    coordinator, sagas, inventory = build()

    await coordinator.pay("tx-1")

    assert sagas.events == ["claim", "open_saga", "mark:APPLIED", "finish"]
    assert len(inventory.calls) == 1


async def test_the_quantities_are_frozen_before_inventory_is_asked():
    # What the saga records and what inventory is told must be the same list, or compensation would put back something different from what was taken.
    coordinator, sagas, inventory = build()

    await coordinator.pay("tx-1")

    saga_id, recorded_lines = sagas.opened[0]
    called_key, called_lines = inventory.calls[0]
    assert called_key == saga_id
    assert called_lines == recorded_lines == [("SKU-1", 3)]


async def test_an_empty_basket_cannot_be_paid_for():
    coordinator, sagas, inventory = build(claimable=False, state=TransactionState("OPEN", 0))

    with pytest.raises(ConflictError) as caught:
        await coordinator.pay("tx-1")

    assert caught.value.error == "EMPTY_BASKET"
    assert inventory.calls == []         # nothing was asked of the other service


async def test_a_paid_basket_cannot_be_paid_again():
    coordinator, sagas, inventory = build(claimable=False, state=TransactionState("COMPLETED", 3))

    with pytest.raises(ConflictError) as caught:
        await coordinator.pay("tx-1")

    assert caught.value.error == "TRANSACTION_NOT_OPEN"
    assert inventory.calls == []


async def test_a_missing_basket_is_a_404():
    coordinator, sagas, inventory = build(claimable=False, state=None)

    with pytest.raises(NotFoundError):
        await coordinator.pay("tx-1")

    assert inventory.calls == []

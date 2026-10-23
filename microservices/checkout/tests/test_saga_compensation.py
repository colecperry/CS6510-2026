# What happens to a payment when the stock step fails, tested without a database or a network.
#
# The rule being checked is that a failed payment leaves nothing behind: the stock is put back if it moved, the basket goes back to the shopper, and the refusal says so rather than the request hanging or crashing.
import pytest
from app.clients.inventory_client import InventoryUnavailable
from app.data.baskets_repo import ReceiptLineRow
from app.saga.coordinator import PaymentCoordinator
from common.errors import ConflictError

from tests.fakes import FakeBaskets, FakeInventory, FakeSagas

LINES = [ReceiptLineRow("SKU-1", "Item 1", 0.85, 2)]


def build(**inventory_kwargs):
    baskets = FakeBaskets(lines=LINES)
    sagas = FakeSagas()
    inventory = FakeInventory(**inventory_kwargs)
    return PaymentCoordinator(baskets, sagas, inventory), sagas, inventory


async def test_a_failed_stock_call_refuses_the_payment():
    coordinator, sagas, inventory = build(reduce_fails=True)

    with pytest.raises(ConflictError) as caught:
        await coordinator.pay("tx-1")

    # The contract has no code for "a service we depend on is down", so a conflict is the least wrong of the three it allows.
    assert caught.value.error == "TRANSACTION_NOT_OPEN"


async def test_a_failed_stock_call_undoes_anything_it_might_have_done():
    # The reduction may or may not have landed: a lost reply looks exactly like a lost request. Undoing the key covers both, because undoing something never applied changes nothing.
    coordinator, sagas, inventory = build(reduce_fails=True)

    with pytest.raises(ConflictError):
        await coordinator.pay("tx-1")

    assert inventory.restored == [sagas.opened[0][0]]


async def test_a_failed_payment_gives_the_basket_back():
    coordinator, sagas, inventory = build(reduce_fails=True)

    with pytest.raises(ConflictError):
        await coordinator.pay("tx-1")

    assert "release" in sagas.events        # paying_since cleared, so the shopper can scan again


async def test_a_shopper_still_gets_a_clean_refusal_when_the_undo_also_fails():
    # Both calls failing is the worst case: nothing can be put right now. The shopper must still be told no rather than seeing a crash, and the saga is left unfinished so recovery picks it up at the next startup.
    coordinator, sagas, inventory = build(reduce_fails=True, restore_fails=True)

    with pytest.raises(ConflictError):
        await coordinator.pay("tx-1")

    assert "release" not in sagas.events    # deliberately not released; see the flag in the write-up


async def test_nothing_is_asked_of_inventory_when_the_basket_cannot_be_claimed():
    coordinator, sagas, inventory = build()
    sagas.claimable = False

    with pytest.raises(Exception):
        await coordinator.pay("tx-1")

    assert inventory.reduced == []
    assert inventory.restored == []

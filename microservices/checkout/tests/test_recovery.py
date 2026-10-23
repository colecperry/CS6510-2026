# Repairing a payment that was interrupted by this service dying, tested without a database or a network.
#
# Compensation cannot help with a crash, because the code that would run it is gone. What survives is the saga row, and recovery has to reach the right ending from that row alone, knowing only what inventory can tell it.
from app.data.saga_repo import UnfinishedSaga
from app.saga.recovery import SagaRecovery

from tests.fakes import FakeBaskets, FakeInventory, FakeSagas

STRANDED = UnfinishedSaga(
    saga_id="saga-1",
    transaction_id="tx-1",
    state="PENDING",
    lines=[("SKU-1", 2)],
    created_at="t0",
)


def build(applied: bool, finishable: bool = True):
    sagas = FakeSagas(unfinished=[STRANDED], finishable=finishable)
    inventory = FakeInventory(applied=applied)
    return SagaRecovery(FakeBaskets(), sagas, inventory), sagas, inventory


async def test_a_payment_whose_stock_never_moved_is_undone():
    # The crash happened before inventory did anything, so there is nothing to put back and the basket simply goes back to the shopper.
    recovery, sagas, inventory = build(applied=False)

    await recovery.recover_once()

    assert "release" in sagas.events
    assert inventory.restored == []        # nothing to undo


async def test_a_payment_whose_stock_did_move_is_completed():
    # Stock is already down, so finishing the sale is what makes the books balance. Putting it back would also balance, but the shopper wanted the items.
    recovery, sagas, inventory = build(applied=True)

    await recovery.recover_once()

    assert "finish" in sagas.events
    assert inventory.restored == []


async def test_stock_goes_back_when_the_sale_can_no_longer_be_finished():
    # The basket was closed some other way, so the only ending left that balances the books is to return the stock.
    recovery, sagas, inventory = build(applied=True, finishable=False)

    await recovery.recover_once()

    assert inventory.restored == ["saga-1"]
    assert "release" in sagas.events


async def test_recovery_is_safe_to_run_twice():
    # It runs at every startup, and a restart loop must not turn one interrupted payment into repeated stock movements.
    recovery, sagas, inventory = build(applied=False)

    await recovery.recover_once()
    await recovery.recover_once()

    assert inventory.restored == []        # still nothing taken, so still nothing put back


async def test_an_unreachable_inventory_leaves_the_saga_for_next_time():
    # Only inventory knows whether stock moved. If it cannot be asked, guessing would be worse than waiting, so the row stays unfinished and the next startup asks again.
    sagas = FakeSagas(unfinished=[STRANDED])
    recovery = SagaRecovery(FakeBaskets(), sagas, FakeInventory(unreachable=True))

    outcome = await recovery.recover_once()

    assert outcome == {"completed": 0, "compensated": 0, "failed": 1}
    assert "release" not in sagas.events   # nothing decided, so nothing changed
    assert "finish" not in sagas.events


async def test_startup_is_not_blocked_by_an_unreachable_inventory():
    # Recovery runs before this service accepts traffic. If it raised instead of recording a failure, a down inventory would stop checkout starting at all.
    sagas = FakeSagas(unfinished=[STRANDED])
    recovery = SagaRecovery(FakeBaskets(), sagas, FakeInventory(unreachable=True))

    await recovery.recover_once()          # must return rather than raise

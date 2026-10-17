# The checkout rules, tested without a database or a web server.
#
# Stand-in repositories hold their data in plain Python, so these run in milliseconds and test the decisions rather than the plumbing. The rules themselves are unchanged from previous weeks; what changed is that they now live in their own service.
import pytest

from common.data.baskets_repo import ScanCounters, TransactionState
from common.data.catalog_repo import CatalogEntry
from common.errors import ConflictError, NotFoundError

from app.service import CheckoutService


class FakeCatalogRepo:
    def __init__(self, entries):
        self._entries = entries

    def get(self, sku):
        return self._entries.get(sku)


class FakeBasketsRepo:
    """Stands in for the database. `state` is what the real table would hold."""

    def __init__(self, state: TransactionState | None):
        self._state = state
        self.lines_added = 0

    async def bump_counters(self, transaction_id, unit_price):
        # The real UPDATE only matches an open basket, so mirror that.
        if self._state is None or self._state.status != "OPEN":
            return None
        return ScanCounters(item_count=1, running_total=unit_price)

    async def complete(self, transaction_id):
        # The real UPDATE also requires the basket to be non-empty.
        if self._state is None or self._state.status != "OPEN" or self._state.item_count == 0:
            return None
        raise AssertionError("not exercised by these tests")

    async def state_of(self, transaction_id):
        return self._state

    async def add_line(self, transaction_id, sku, name, unit_price):
        self.lines_added += 1


class FakeScanSink:
    """Stands in for whatever carries scans away to be counted."""

    def __init__(self):
        self.scans = []

    # Not async, matching the real one: handing a scan over must not wait.
    def record(self, sku):
        self.scans.append(sku)


CATALOG = FakeCatalogRepo({"SKU-1": CatalogEntry("SKU-1", "Item 1", 0.85)})


def build(state):
    baskets = FakeBasketsRepo(state)
    scans = FakeScanSink()
    return CheckoutService(baskets, CATALOG, scans), baskets, scans


async def test_scanning_an_unknown_item_is_refused():
    service, _, _ = build(TransactionState("OPEN", 0))

    with pytest.raises(NotFoundError) as caught:
        await service.scan("tx-1", "SKU-does-not-exist")

    assert caught.value.error == "UNKNOWN_SKU"


async def test_scanning_a_closed_basket_is_refused():
    service, _, _ = build(TransactionState("COMPLETED", 3))

    with pytest.raises(ConflictError) as caught:
        await service.scan("tx-1", "SKU-1")

    assert caught.value.error == "TRANSACTION_NOT_OPEN"


async def test_scanning_a_missing_basket_is_refused():
    # No row at all, as opposed to a row that is closed. The database says no to both; only a follow-up question tells them apart.
    service, _, _ = build(None)

    with pytest.raises(NotFoundError) as caught:
        await service.scan("tx-1", "SKU-1")

    assert caught.value.error == "NOT_FOUND"


async def test_a_successful_scan_is_handed_on_to_be_counted():
    service, baskets, scans = build(TransactionState("OPEN", 0))

    result = await service.scan("tx-1", "SKU-1")

    assert result.name == "Item 1"
    assert result.unit_price == 0.85
    assert baskets.lines_added == 1
    assert scans.scans == ["SKU-1"]


async def test_an_empty_basket_cannot_be_paid_for():
    service, _, _ = build(TransactionState("OPEN", 0))

    with pytest.raises(ConflictError) as caught:
        await service.complete("tx-1")

    assert caught.value.error == "EMPTY_BASKET"


async def test_a_paid_basket_cannot_be_paid_again():
    service, _, _ = build(TransactionState("COMPLETED", 3))

    with pytest.raises(ConflictError) as caught:
        await service.complete("tx-1")

    assert caught.value.error == "TRANSACTION_NOT_OPEN"

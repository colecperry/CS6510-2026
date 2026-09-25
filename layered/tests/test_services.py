# Tests for the checkout and reporting rules, with no database and no web
# server involved.
#
# This is what the layering buys: the services take their repositories as
# arguments, so a test can pass stand-ins that hold data in plain Python
# and assert on the rules alone. These run in milliseconds.
import pytest

from app.analytics.service import AnalyticsService
from app.data.catalog_repo import CatalogEntry
from app.data.transactions_repo import ScanCounters, TransactionState
from app.errors import ConflictError, NotFoundError
from app.transactions.service import TransactionService


class FakeCatalogRepo:
    def __init__(self, entries):
        self._entries = entries

    def get(self, sku):
        return self._entries.get(sku)

    def all(self):
        return list(self._entries.values())


class FakeTransactionsRepo:
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


class FakeAnalyticsService:
    def __init__(self):
        self.scans = []

    async def record_scan(self, sku):
        self.scans.append(sku)


CATALOG = FakeCatalogRepo({"SKU-1": CatalogEntry("SKU-1", "Item 1", 0.85)})


def build(state):
    analytics = FakeAnalyticsService()
    repo = FakeTransactionsRepo(state)
    return TransactionService(repo, CATALOG, analytics), repo, analytics


async def test_scanning_an_unknown_sku_is_not_found():
    service, _, _ = build(TransactionState("OPEN", 0))

    with pytest.raises(NotFoundError) as caught:
        await service.scan("tx-1", "SKU-does-not-exist")

    assert caught.value.error == "UNKNOWN_SKU"


async def test_scanning_a_closed_basket_is_a_conflict():
    service, _, _ = build(TransactionState("COMPLETED", 3))

    with pytest.raises(ConflictError) as caught:
        await service.scan("tx-1", "SKU-1")

    assert caught.value.error == "TRANSACTION_NOT_OPEN"


async def test_scanning_a_missing_basket_is_not_found():
    # No row at all, as opposed to a row that is closed.
    service, _, _ = build(None)

    with pytest.raises(NotFoundError) as caught:
        await service.scan("tx-1", "SKU-1")

    assert caught.value.error == "NOT_FOUND"


async def test_a_successful_scan_feeds_the_popularity_window():
    service, repo, analytics = build(TransactionState("OPEN", 0))

    result = await service.scan("tx-1", "SKU-1")

    assert result.name == "Item 1"
    assert result.unit_price == 0.85
    assert repo.lines_added == 1
    assert analytics.scans == ["SKU-1"]


async def test_completing_an_empty_basket_is_a_conflict():
    service, _, _ = build(TransactionState("OPEN", 0))

    with pytest.raises(ConflictError) as caught:
        await service.complete("tx-1")

    assert caught.value.error == "EMPTY_BASKET"


async def test_completing_an_already_paid_basket_is_a_conflict():
    service, _, _ = build(TransactionState("COMPLETED", 3))

    with pytest.raises(ConflictError) as caught:
        await service.complete("tx-1")

    assert caught.value.error == "TRANSACTION_NOT_OPEN"


class FakeAnalyticsRepo:
    """Counts how often the ranking is rebuilt."""

    def __init__(self, claim_on):
        self._claim_on = claim_on  # sequence numbers that win the rebuild
        self._seq = 0
        self.rebuilds = 0

    async def log_scan(self, sku):
        self._seq += 1
        return self._seq

    async def claim_recompute(self, seq):
        return (0, seq) if seq in self._claim_on else None

    async def count_window(self, start, end, cap):
        return []

    async def publish_ranking(self, ranked, prune_before):
        self.rebuilds += 1


async def test_ranking_is_rebuilt_only_when_due():
    repo = FakeAnalyticsRepo(claim_on={3})
    service = AnalyticsService(repo, CATALOG, low_stock_threshold=50, snapshot_cap=100)

    for _ in range(5):
        await service.record_scan("SKU-1")

    # Five scans, one rebuild: the other four took the cheap path.
    assert repo.rebuilds == 1

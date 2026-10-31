# The stock reporting rules, tested without a database.
#
# Only one real decision lives in this service: which threshold to use when the caller does not give one. The rest is shaping rows into a response.
from app.data.catalog_repo import CatalogEntry
from app.data.stock_repo import LowStockRow
from app.service import InventoryService


class FakeCatalogRepo:
    def all(self):
        return [CatalogEntry("SKU-1", "Item 1", 0.85), CatalogEntry("SKU-2", "Item 2", 1.20)]


class FakeStockRepo:
    """Records the threshold it was asked for, so the test can check it."""

    def __init__(self):
        self.asked_for = None

    async def below_threshold(self, threshold):
        self.asked_for = threshold
        return [LowStockRow("SKU-1", "Item 1", 3)]


def build():
    stock = FakeStockRepo()
    return InventoryService(FakeCatalogRepo(), stock, default_threshold=50), stock


async def test_the_configured_threshold_is_used_when_the_caller_gives_none():
    service, stock = build()

    await service.low_stock(None)

    assert stock.asked_for == 50


async def test_the_callers_threshold_wins_when_given():
    service, stock = build()

    await service.low_stock(9999)

    assert stock.asked_for == 9999


async def test_an_alert_reports_the_threshold_it_was_measured_against():
    # The caller needs to know what "low" meant for this answer, not just which items qualified.
    service, _ = build()

    response = await service.low_stock(9999)

    assert response.threshold == 9999
    assert response.alerts[0].threshold == 9999
    assert response.alerts[0].current_stock == 3


async def test_the_product_list_comes_from_memory():
    # No await anywhere: this reads the catalogue copy loaded at startup, so it never touches the database.
    service, _ = build()

    response = service.list_items()

    assert len(response.items) == 2
    assert response.items[0].sku == "SKU-1"

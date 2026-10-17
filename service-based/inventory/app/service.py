# What the shop sells, and what is running out.
#
# Reads only. The stock levels it reports on are written by the checkout service when a basket is paid for; the two never speak, they just use the same database.
from datetime import datetime, timezone

from common.data.catalog_repo import CatalogRepo
from common.data.stock_repo import StockRepo
from common.schemas import CatalogItem, CatalogResponse, LowStockAlert, LowStockResponse


class InventoryService:
    def __init__(self, catalog: CatalogRepo, stock: StockRepo, default_threshold: int):
        self._catalog = catalog                      # the product list, from memory
        self._stock = stock                          # live counts, from the database
        self._default_threshold = default_threshold

    def list_items(self) -> CatalogResponse:
        """Returns the whole product list."""
        # Served from memory, so this makes no database call at all. The load client asks for it once at startup to learn what barcodes exist.
        return CatalogResponse(
            items=[
                CatalogItem(sku=e.sku, name=e.name, price=e.price)
                for e in self._catalog.all()
            ]
        )

    async def low_stock(self, threshold: int | None) -> LowStockResponse:
        """Lists items running out. Falls back to the configured threshold."""
        effective = threshold if threshold is not None else self._default_threshold
        rows = await self._stock.below_threshold(effective)

        # Worked out fresh each request rather than stored, so there is no alert history that could disagree with the actual stock level.
        now = datetime.now(timezone.utc)
        return LowStockResponse(
            threshold=effective,
            generated_at=now,
            alerts=[
                LowStockAlert(
                    sku=r.sku,
                    name=r.name,
                    current_stock=r.qty,
                    threshold=effective,
                    triggered_at=now,
                )
                for r in rows
            ],
        )

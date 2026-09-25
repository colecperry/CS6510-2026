# The only place that reads the catalog table.
#
# Item names and prices are needed on every scan, so the whole catalog is
# loaded into memory at startup and served from there. Nothing in the API can
# change the catalog, so that copy can never go stale.
from typing import NamedTuple

import asyncpg


# One catalog row. A tuple, so it is cheap to pass around, but with names.
class CatalogEntry(NamedTuple):
    sku: str
    name: str
    price: float


class CatalogRepo:
    def __init__(self, pool: asyncpg.Pool):
        self._pool = pool
        self._by_sku: dict[str, CatalogEntry] = {}

    async def load(self) -> None:
        """Reads the whole catalog into memory. Call once, at startup."""
        rows = await self._pool.fetch("SELECT sku, name, price FROM catalog")
        # float() here so Postgres Decimals never escape the data layer.
        self._by_sku = {
            row["sku"]: CatalogEntry(row["sku"], row["name"], float(row["price"]))
            for row in rows
        }

    # Not async on purpose - this is a memory read, not a database call.
    def get(self, sku: str) -> CatalogEntry | None:
        """Looks up one item. Returns None if the SKU does not exist."""
        return self._by_sku.get(sku)

    def all(self) -> list[CatalogEntry]:
        """Returns every catalog entry, for GET /items."""
        return list(self._by_sku.values())

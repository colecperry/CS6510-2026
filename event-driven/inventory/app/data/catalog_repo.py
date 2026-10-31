# The only place that reads this service's catalog table.
#
# Inventory owns the authoritative catalogue. Checkout and analytics hold their own read-only copies, seeded from the same data, because neither may reach this database.
# The whole table is loaded into memory at startup, and nothing in the API can change it, so that copy can never go stale.
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
        self._by_sku: dict[str, CatalogEntry] = {}   # filled by load()

    async def load(self) -> None:
        """Reads the whole catalog into memory. Call once, at startup."""
        rows = await self._pool.fetch("SELECT sku, name, price FROM catalog")
        self._by_sku = {
            # float() here, so prices leave the data layer as plain numbers.
            row["sku"]: CatalogEntry(row["sku"], row["name"], float(row["price"]))
            for row in rows
        }

    def all(self) -> list[CatalogEntry]:
        """Returns every catalog entry, for GET /items."""
        return list(self._by_sku.values())

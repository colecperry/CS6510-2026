# This service's own read-only copy of the catalogue, used for prices on every scan and every receipt line.
#
# Inventory owns the authoritative catalogue and this service cannot reach it, so the rows are loaded here at startup from checkout's own database.
# Copying is safe only because catalogue rows never change while the system is running. A lookup to inventory instead would put a network call on the most frequent operation in the system.
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

    # Not async on purpose: this reads memory, not the database. Every scan calls it, so it should not pretend to be something that waits.
    def get(self, sku: str) -> CatalogEntry | None:
        """Looks up one item. Returns None if the SKU does not exist."""
        return self._by_sku.get(sku)

    def all(self) -> list[CatalogEntry]:
        """Returns every catalog entry, for GET /items."""
        return list(self._by_sku.values())

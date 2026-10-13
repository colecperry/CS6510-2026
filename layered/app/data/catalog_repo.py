# Read-through cache of the catalog table. Loaded once at startup, then only
# ever read, because no endpoint in the spec mutates the catalog.
from typing import NamedTuple

import asyncpg


# A tuple under the hood, so unpacking and indexing cost nothing extra, but
# with names so callers read `entry.price` instead of `entry[2]`.
class CatalogEntry(NamedTuple):
    sku: str
    name: str
    price: float


class CatalogRepo:
    def __init__(self, pool: asyncpg.Pool):
        """Stores the pool for the one query this repo ever runs.

        Takes: pool - the shared connection pool.
        Returns: nothing.
        """
        self._pool = pool
        self._by_sku: dict[str, CatalogEntry] = {}

    # Called once, from main.py's lifespan, before the app serves traffic.
    async def load(self) -> None:
        """Reads the whole catalog table into memory.

        Takes: nothing.
        Returns: nothing - fills this repo's internal dict.
        """
        rows = await self._pool.fetch("SELECT sku, name, price FROM catalog")
        # Postgres NUMERIC arrives as Decimal. Casting to float once here is
        # what keeps the rest of the app from doing it at every call site,
        # which week 1 had to do in about eight places.
        self._by_sku = {
            row["sku"]: CatalogEntry(row["sku"], row["name"], float(row["price"]))
            for row in rows
        }

    # Deliberately not async: this is a dict lookup, not a database call.
    # The scan path hits this on every single scanned item, so keeping it a
    # plain function avoids creating a coroutine roughly 1,200 times a second.
    def get(self, sku: str) -> CatalogEntry | None:
        """Looks up one item by SKU.

        Takes: sku - the scanned barcode.
        Returns: the CatalogEntry, or None if no such SKU exists.
        """
        return self._by_sku.get(sku)

    def all(self) -> list[CatalogEntry]:
        """Returns every catalog entry, for GET /items.

        Takes: nothing.
        Returns: a list of every CatalogEntry, in insertion order.
        """
        return list(self._by_sku.values())

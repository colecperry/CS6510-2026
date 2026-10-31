# This service's own read-only copy of the catalogue, used only to put item names on the ranking.
#
# Inventory owns the authoritative catalogue and this service cannot reach it, so the rows are loaded here at startup from its own database. Safe because catalogue rows never change while the system is running.
import asyncpg


class CatalogRepo:
    def __init__(self, pool: asyncpg.Pool):
        self._pool = pool
        self._names: dict[str, str] = {}     # filled by load()

    async def load(self) -> None:
        """Reads every item name into memory. Call once, at startup."""
        rows = await self._pool.fetch("SELECT sku, name FROM catalog")
        self._names = {row["sku"]: row["name"] for row in rows}

    # Not async on purpose: this reads memory, not the database, and should not pretend to be something that waits.
    def name_for(self, sku: str) -> str:
        """The display name for a SKU, falling back to the code itself if it is somehow unknown."""
        # A scan for an unknown SKU cannot normally reach the log, because checkout rejects it first. Falling back rather than raising means one odd row could never stop the whole ranking being published.
        return self._names.get(sku, sku)

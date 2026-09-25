# In-memory copy of the catalog table, so scans never hit Postgres to find
# an item's name and price.
#
# Filled once at startup by main.py, then only ever read. Safe because no
# endpoint in the contract can change the catalog.
import app.pool as pool_module

# sku -> (name, price)
catalog: dict[str, tuple[str, float]] = {}


async def load() -> None:
    """Reads the whole catalog into memory. Call once, at startup."""
    rows = await pool_module.pool.fetch("SELECT sku, name, price FROM catalog")
    for row in rows:
        # float() so Postgres Decimals do not leak into the rest of the app.
        catalog[row["sku"]] = (row["name"], float(row["price"]))

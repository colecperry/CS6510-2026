# In-memory copy of the catalog table, so we don't hit Postgres on every scan.
import app.pool as pool_module

# sku -> (name, price). Filled once at startup by load(), then only ever read.
catalog: dict[str, tuple[str, float]] = {}


async def load() -> None:
    """Loads the full catalog table into memory, once at startup.

    Takes: nothing.
    Returns: nothing - fills the module-level `catalog` dict.
    """
    # Read the whole catalog table once, and copy it into the dict above.
    rows = await pool_module.pool.fetch("SELECT sku, name, price FROM catalog")
    for row in rows:
        # Postgres NUMERIC comes back as Decimal - cast to float for the API.
        catalog[row["sku"]] = (row["name"], float(row["price"]))

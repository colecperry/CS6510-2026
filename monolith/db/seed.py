# Rebuilds the database from scratch: schema, then starting catalog/stock data.
import asyncio
from pathlib import Path

import app.pool as pool_module

SCHEMA_PATH = Path(__file__).parent / "schema.sql"
CATALOG_SIZE = 2000
STARTING_STOCK = 10000


# Same formula as mockserver/MockServer.java, so both servers agree.
def build_catalog() -> list[tuple[str, str, float]]:
    """Builds the 2000-item catalog's SKU/name/price data.

    Takes: nothing.
    Returns: a list of (sku, name, price) tuples.
    """
    rows = []
    for i in range(1, CATALOG_SIZE + 1):
        sku = f"SKU-{i:06d}"
        name = f"Item {i}"
        price = round(0.5 + (i % 47) * 0.35, 2)
        rows.append((sku, name, price))
    return rows


async def seed() -> None:
    """Rebuilds the database schema and loads starting catalog/stock data.

    Takes: nothing.
    Returns: nothing.
    """
    await pool_module.connect()
    conn = pool_module.pool

    # Drop and recreate every table, so this always starts from empty.
    await conn.execute(SCHEMA_PATH.read_text())

    # Bulk-load the catalog via Postgres's COPY protocol, not one INSERT each.
    catalog_rows = build_catalog()
    await conn.copy_records_to_table(
        "catalog", records=catalog_rows, columns=["sku", "name", "price"]
    )

    # Give every SKU the same starting stock.
    stock_rows = [(sku, STARTING_STOCK) for sku, _, _ in catalog_rows]
    await conn.copy_records_to_table("stock", records=stock_rows, columns=["sku", "qty"])

    print(f"Seeded {len(catalog_rows)} catalog items with {STARTING_STOCK} stock each.")
    await pool_module.disconnect()


if __name__ == "__main__":
    asyncio.run(seed())

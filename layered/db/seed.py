# Wipes the database and rebuilds it to a known starting state.
#
# Run before every load test, via scripts/reset_db.sh. Two test runs are only
# comparable if they both start from identical data.
import asyncio
from pathlib import Path

from app.data.pool import close_pool, create_pool

SCHEMA_PATH = Path(__file__).parent / "schema.sql"
CATALOG_SIZE = 2000
STARTING_STOCK = 10000


def build_catalog() -> list[tuple[str, str, float]]:
    """Builds the 2000 (sku, name, price) rows the catalog starts with."""
    # Same formula as mockserver/MockServer.java, so the reference server and
    # this one agree on what every SKU costs.
    rows = []
    for i in range(1, CATALOG_SIZE + 1):
        sku = f"SKU-{i:06d}"
        name = f"Item {i}"
        price = round(0.5 + (i % 47) * 0.35, 2)
        rows.append((sku, name, price))
    return rows


async def seed() -> None:
    """Drops every table, recreates it, and loads the starting data."""
    pool = await create_pool()
    try:
        await pool.execute(SCHEMA_PATH.read_text())

        # copy_records_to_table uses Postgres's bulk COPY, which loads 2000
        # rows in one round trip instead of 2000 separate INSERTs.
        catalog_rows = build_catalog()
        await pool.copy_records_to_table(
            "catalog", records=catalog_rows, columns=["sku", "name", "price"]
        )

        stock_rows = [(sku, STARTING_STOCK) for sku, _, _ in catalog_rows]
        await pool.copy_records_to_table("stock", records=stock_rows, columns=["sku", "qty"])

        print(f"Seeded {len(catalog_rows)} catalog items with {STARTING_STOCK} stock each.")
    finally:
        # Close even if the schema failed to apply, so a bad SQL file does
        # not leave this script hanging on open connections.
        await close_pool(pool)


if __name__ == "__main__":
    asyncio.run(seed())

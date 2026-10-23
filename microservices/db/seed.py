# Wipes all three databases and rebuilds them to a known starting state.
#
# Run before every load test, via scripts/reset_db.sh. Two runs are only comparable if they both start from identical data, and this week that means three stores agreeing rather than one being correct.
import asyncio
import os
from pathlib import Path

import asyncpg
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

CATALOG_SIZE = 2000
STARTING_STOCK = 10000

# Each entry is one database: the env var holding its URL, and the schema that builds it.
STORES = {
    "checkout": "CHECKOUT_DATABASE_URL",
    "inventory": "INVENTORY_DATABASE_URL",
    "analytics": "ANALYTICS_DATABASE_URL",
}

# Connects directly rather than through common/, because this script has to work before the services exist and must not break when that package changes.


def build_catalog() -> list[tuple[str, str, float]]:
    """Builds the 2000 (sku, name, price) rows every store starts with."""
    # Same formula as mockserver/MockServer.java, so the reference server and this one agree on prices.
    rows = []
    for i in range(1, CATALOG_SIZE + 1):
        sku = f"SKU-{i:06d}"
        name = f"Item {i}"
        price = round(0.5 + (i % 47) * 0.35, 2)
        rows.append((sku, name, price))
    return rows


async def seed_store(name: str, url: str, catalog_rows: list[tuple[str, str, float]]) -> int:
    """Applies one store's schema and loads its starting data. Returns its catalogue row count."""
    schema = (Path(__file__).parent / name / "schema.sql").read_text()
    conn = await asyncpg.connect(url)
    try:
        await conn.execute(schema)  # drops and recreates everything

        # Every store gets the identical catalogue. It never changes at runtime, which is what makes holding three copies safe.
        await conn.copy_records_to_table(
            "catalog", records=catalog_rows, columns=["sku", "name", "price"]
        )

        # Stock exists in inventory alone. That is the whole reason payment needs coordinating.
        if name == "inventory":
            stock_rows = [(sku, STARTING_STOCK) for sku, _, _ in catalog_rows]
            await conn.copy_records_to_table("stock", records=stock_rows, columns=["sku", "qty"])

        return await conn.fetchval("SELECT count(*) FROM catalog")
    finally:
        # Close even if the schema failed, so a bad SQL file does not leave this script holding a connection.
        await conn.close()


async def seed() -> None:
    catalog_rows = build_catalog()
    counts = {}

    for name, env_var in STORES.items():
        url = os.environ.get(env_var)
        if not url:
            raise SystemExit(f"{env_var} is not set. Copy .env.example to .env first.")
        counts[name] = await seed_store(name, url, catalog_rows)
        print(f"  {name:<10} seeded, {counts[name]} catalog rows")

    # Three copies that disagree would let a receipt charge one price while the ranking reports another, and nothing at runtime would notice. Cheaper to refuse to start than to debug that later.
    if len(set(counts.values())) != 1:
        raise SystemExit(f"catalogue copies disagree: {counts}")

    print(f"\nAll three stores seeded: {CATALOG_SIZE} catalog items, "
          f"{STARTING_STOCK} stock each in inventory.")


if __name__ == "__main__":
    asyncio.run(seed())

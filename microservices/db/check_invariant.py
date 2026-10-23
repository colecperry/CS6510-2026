# Checks that every item sold came off the shelf, across two databases.
#
# The previous weeks did this in one SQL query, because units sold and units remaining lived in the same place. They no longer do, so this reads each store separately and compares the two outside the database.
#
# That is a real loss worth naming: a property the database used to enforce is now only checkable by a program that can see both halves.
import asyncio
import os
from pathlib import Path

import asyncpg
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

STARTING_STOCK = 10000


async def units_sold() -> dict[str, int]:
    """How many units of each SKU left the shop in completed transactions, from the checkout store."""
    conn = await asyncpg.connect(os.environ["CHECKOUT_DATABASE_URL"])
    try:
        rows = await conn.fetch(
            """
            SELECT ti.sku, count(*) AS sold
            FROM transaction_items ti
            JOIN transactions t ON t.transaction_id = ti.transaction_id
            WHERE t.status = 'COMPLETED'
            GROUP BY ti.sku
            """
        )
        return {r["sku"]: r["sold"] for r in rows}
    finally:
        await conn.close()


async def stock_left() -> dict[str, int]:
    """How many units of each SKU remain, from the inventory store."""
    conn = await asyncpg.connect(os.environ["INVENTORY_DATABASE_URL"])
    try:
        rows = await conn.fetch("SELECT sku, qty FROM stock")
        return {r["sku"]: r["qty"] for r in rows}
    finally:
        await conn.close()


async def saga_outcomes() -> dict[str, int]:
    """How each payment ended, so compensations can be reported rather than assumed to be zero."""
    conn = await asyncpg.connect(os.environ["CHECKOUT_DATABASE_URL"])
    try:
        rows = await conn.fetch("SELECT state, count(*) AS n FROM payment_saga GROUP BY state")
        return {r["state"]: r["n"] for r in rows}
    finally:
        await conn.close()


async def main() -> None:
    sold, left, sagas = await units_sold(), await stock_left(), await saga_outcomes()

    negative = exact = sold_out = unexplained = 0
    offenders = []

    for sku, qty in left.items():
        taken = STARTING_STOCK - qty
        expected = sold.get(sku, 0)
        if qty < 0:
            negative += 1
        if taken == expected:
            exact += 1
        elif qty == 0 and expected > STARTING_STOCK:
            # The contract defines no out-of-stock response, so a sold-out item floors at zero and the count of units sold runs past what was ever on the shelf. Every previous week recorded the same case.
            sold_out += 1
        else:
            unexplained += 1
            if len(offenders) < 5:
                offenders.append(f"{sku}: taken {taken}, sold {expected}, left {qty}")

    print("Stock invariant, checked across the checkout and inventory stores")
    print(f"  SKUs negative:              {negative}")
    print(f"  SKUs exact:                 {exact}")
    print(f"  explained by selling out:   {sold_out}")
    print(f"  unexplained violations:     {unexplained}")
    for line in offenders:
        print(f"      {line}")

    print("\nPayments, by how they ended")
    for state in ("DONE", "COMPENSATED", "PENDING", "APPLIED"):
        print(f"  {state:<12} {sagas.get(state, 0)}")
    # Anything still PENDING or APPLIED is a payment left unfinished, which recovery should have cleared at the last startup.
    stuck = sagas.get("PENDING", 0) + sagas.get("APPLIED", 0)

    if unexplained or negative or stuck:
        raise SystemExit(1)


asyncio.run(main())

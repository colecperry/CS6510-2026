# Checks that every item sold came off the shelf, across two databases.
#
# The earlier weeks did this in one SQL query, because units sold and units remaining lived in the same place. They no longer do, so this reads each store separately and compares the two outside the database.
#
# This week adds a second complication. Stock comes down after the sale rather than during it, so a difference may mean a message is still in flight rather than that something is wrong. The check therefore waits for the outbox to empty before it compares anything.
import asyncio
import os
from pathlib import Path

import asyncpg
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

STARTING_STOCK = 10000

# How long to let the system settle before deciding a difference is real. Generous: a backlog built up during a 120 second run at full load still drains inside this.
DRAIN_TIMEOUT_SECONDS = 120


async def wait_for_outbox_to_drain() -> int:
    """Waits until checkout owes no more messages, and returns how long it took in seconds."""
    # Without this the check would report a violation for every sale whose message had not yet been delivered, which at the end of a load run is most of the recent ones.
    conn = await asyncpg.connect(os.environ["CHECKOUT_DATABASE_URL"])
    try:
        for second in range(DRAIN_TIMEOUT_SECONDS):
            pending = await conn.fetchval(
                "SELECT count(*) FROM outbox WHERE sent_at IS NULL"
            )
            if pending == 0:
                return second
            await asyncio.sleep(1)
        raise SystemExit(
            f"outbox still holding messages after {DRAIN_TIMEOUT_SECONDS}s: "
            "the relay is not running, or the broker is down"
        )
    finally:
        await conn.close()


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


async def sales_applied() -> tuple[int, int]:
    """How many sales checkout announced, and how many inventory acted on."""
    # These two should match once everything has settled. They are read rather than assumed because a gap is the clearest possible sign that a message was delivered but not handled.
    checkout = await asyncpg.connect(os.environ["CHECKOUT_DATABASE_URL"])
    inventory = await asyncpg.connect(os.environ["INVENTORY_DATABASE_URL"])
    try:
        announced = await checkout.fetchval("SELECT count(*) FROM outbox")
        applied = await inventory.fetchval("SELECT count(*) FROM applied_requests")
        return announced, applied
    finally:
        await checkout.close()
        await inventory.close()


async def main() -> None:
    waited = await wait_for_outbox_to_drain()
    # Messages are confirmed by the broker before the outbox row is marked sent, but inventory handles them a moment later still. This is the gap between "the broker has it" and "the stock has moved".
    await asyncio.sleep(2)

    sold, left = await units_sold(), await stock_left()
    announced, applied = await sales_applied()

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

    print(f"Outbox drained after {waited}s")
    print("Stock invariant, checked across the checkout and inventory stores")
    print(f"  SKUs negative:              {negative}")
    print(f"  SKUs exact:                 {exact}")
    print(f"  explained by selling out:   {sold_out}")
    print(f"  unexplained violations:     {unexplained}")
    for line in offenders:
        print(f"      {line}")

    print("\nSales, announced against applied")
    print(f"  announced by checkout:      {announced}")
    print(f"  applied by inventory:       {applied}")
    undelivered = announced - applied

    if unexplained or negative or undelivered:
        raise SystemExit(1)


asyncio.run(main())

# Buys baskets through the edge and reports what the shopper saw, for the outage scenarios.
#
# Used while a service or the broker is deliberately stopped, so the thing it checks is that the shopper's request still succeeded and the receipt was complete. Nothing here looks at stock: whether that caught up is a separate question, asked after the outage ends.
import asyncio
import sys

import httpx

BASE = "http://localhost:8080"
SKUS = [f"SKU-{n:06d}" for n in range(1, 6)]


async def buy_one(client: httpx.AsyncClient, items: int) -> tuple[int, int]:
    """Buys one basket and returns the payment's status code and how many lines the receipt carried."""
    started = await client.post(f"{BASE}/transactions", json={"stationId": "scenario"})
    transaction_id = started.json()["transactionId"]
    for n in range(items):
        await client.post(
            f"{BASE}/transactions/{transaction_id}/items",
            json={"sku": SKUS[n % len(SKUS)], "quantity": 1},
        )
    paid = await client.post(f"{BASE}/transactions/{transaction_id}/complete")
    lines = len(paid.json().get("lines", [])) if paid.status_code == 200 else 0
    return paid.status_code, lines


async def main() -> None:
    baskets = int(sys.argv[1]) if len(sys.argv) > 1 else 20
    items = int(sys.argv[2]) if len(sys.argv) > 2 else 3

    async with httpx.AsyncClient(timeout=30.0) as client:
        results = [await buy_one(client, items) for _ in range(baskets)]

    paid = sum(1 for status, _ in results if status == 200)
    complete = sum(1 for status, lines in results if status == 200 and lines == min(items, len(SKUS)))
    print(f"  baskets paid for:        {paid}/{baskets}")
    print(f"  receipts fully itemised: {complete}/{baskets}")
    if paid != baskets:
        print("  codes seen:", sorted({status for status, _ in results}))
        raise SystemExit(1)


asyncio.run(main())

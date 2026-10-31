#!/usr/bin/env bash
# Measures how long stock takes to catch up with a sale.
#
# This number only exists this week. Until now paying reduced stock inside the request, so the lag was zero by construction; a sale is now announced and applied afterwards, and the spec requires the delay to be stated rather than guessed.
# Buys one item through the edge, then polls the inventory store until the reduction lands.
set -u
cd "$(dirname "$0")/.."
uv run python - "$@" <<'PY'
import asyncio
import os
import sys
import time
from pathlib import Path

import asyncpg
import httpx
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env" if (Path(__file__).parent / ".env").exists() else Path(".env"))

BASE = os.environ.get("BASE", "http://localhost:8080")
SKU = "SKU-000001"
ROUNDS = int(sys.argv[1]) if len(sys.argv) > 1 else 5


async def one_round(client: httpx.AsyncClient, conn: asyncpg.Connection) -> float:
    """Buys one unit and returns the seconds between the receipt and the stock moving."""
    before = await conn.fetchval("SELECT qty FROM stock WHERE sku = $1", SKU)

    started = await client.post(f"{BASE}/transactions", json={"stationId": "lag"})
    transaction_id = started.json()["transactionId"]
    await client.post(f"{BASE}/transactions/{transaction_id}/items", json={"sku": SKU, "quantity": 1})

    paid_at = time.perf_counter()
    await client.post(f"{BASE}/transactions/{transaction_id}/complete")

    # Polls rather than waits for a signal, because nothing tells checkout that inventory acted. That is the whole shape of the change: the announcement is one way.
    while True:
        if await conn.fetchval("SELECT qty FROM stock WHERE sku = $1", SKU) < before:
            return time.perf_counter() - paid_at
        if time.perf_counter() - paid_at > 30:
            raise SystemExit("stock never moved: the relay, the broker or inventory is down")
        await asyncio.sleep(0.002)


async def main() -> None:
    conn = await asyncpg.connect(os.environ["INVENTORY_DATABASE_URL"])
    async with httpx.AsyncClient(timeout=10.0) as client:
        lags = [await one_round(client, conn) for _ in range(ROUNDS)]
    await conn.close()

    lags.sort()
    print(f"Lag between the receipt and the stock reduction, over {ROUNDS} sales")
    for lag in lags:
        print(f"  {lag * 1000:7.1f} ms")
    print(f"\n  median {lags[len(lags) // 2] * 1000:.1f} ms    worst {lags[-1] * 1000:.1f} ms")


asyncio.run(main())
PY

# Measures how long a scan takes through the edge, one at a time, for the analytics outage scenario.
#
# The claim being tested is that a scan costs the same whether or not anything is listening for it, so this is run twice and the two numbers compared. Deliberately sequential: a closed loop of one station measures the path itself rather than how the system shares load.
import asyncio
import sys
import time

import httpx

BASE = "http://localhost:8080"


async def main() -> None:
    seconds = float(sys.argv[1]) if len(sys.argv) > 1 else 10.0

    async with httpx.AsyncClient(timeout=30.0) as client:
        started = await client.post(f"{BASE}/transactions", json={"stationId": "latency"})
        transaction_id = started.json()["transactionId"]

        samples: list[float] = []
        until = time.perf_counter() + seconds
        while time.perf_counter() < until:
            at = time.perf_counter()
            await client.post(
                f"{BASE}/transactions/{transaction_id}/items",
                json={"sku": "SKU-000001", "quantity": 1},
            )
            samples.append((time.perf_counter() - at) * 1000)

    samples.sort()
    print(f"  scans:  {len(samples)}")
    print(f"  median: {samples[len(samples) // 2]:.2f} ms")
    print(f"  p95:    {samples[int(len(samples) * 0.95)]:.2f} ms")


asyncio.run(main())

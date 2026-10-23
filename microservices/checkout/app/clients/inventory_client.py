# Talks to the inventory service, which owns the stock this service needs to reduce when a basket is paid for.
#
# Every network detail lives here, so the payment sequence next door reads as a list of steps rather than a list of HTTP calls.
# Nothing in this file decides anything: it asks, retries while it is worth retrying, and reports what came back.
import asyncio
import time

import httpx


class InventoryUnavailable(Exception):
    """Inventory could not be reached within the retry budget. The caller decides what that means for the payment."""


class InventoryClient:
    def __init__(self, base_url: str, timeout_ms: int, retry_budget_ms: int):
        self._base = base_url.rstrip("/")
        self._budget = retry_budget_ms / 1000
        # One client for the process, so connections are reused rather than reopened on every payment.
        self._client = httpx.AsyncClient(timeout=timeout_ms / 1000)

    async def reduce_stock(self, key: str, lines: list[tuple[str, int]]) -> None:
        """Asks inventory to take these quantities off the shelf. Safe to call twice with the same key."""
        await self._post(
            "/internal/stock/reduce",
            {
                "idempotencyKey": key,
                "lines": [{"sku": sku, "qty": qty} for sku, qty in lines],
            },
        )

    async def restore_stock(self, key: str) -> None:
        """Asks inventory to put back whatever the named request took."""
        # One attempt only. This is best-effort cleanup after something already failed, and recovery will repeat it at the next startup, so spending the whole retry budget again here would only double how long the shopper waits to be told no.
        await self._post("/internal/stock/restore", {"idempotencyKey": key}, retry=False)

    async def was_applied(self, key: str) -> bool:
        """Asks whether a stock reduction ever landed. Used when a payment was interrupted and its outcome is unknown."""
        result = await self._get(f"/internal/stock/applied/{key}")
        return result["applied"]

    async def _post(self, path: str, body: dict, retry: bool = True) -> dict:
        return await self._with_retries(
            lambda: self._client.post(self._base + path, json=body), retry
        )

    async def _get(self, path: str, retry: bool = True) -> dict:
        return await self._with_retries(lambda: self._client.get(self._base + path), retry)

    async def _with_retries(self, send, retry: bool = True) -> dict:
        """Keeps trying until the budget runs out, then gives up so the caller can refuse cleanly."""
        # Bounded on purpose. The load client abandons a request after 10 seconds, and a request it abandoned leaves no server-side evidence of what went wrong. Giving up first turns a dead dependency into a refusal we can explain.
        deadline = time.monotonic() + (self._budget if retry else 0)
        delay = 0.05
        last = None
        while True:
            try:
                response = await send()
                response.raise_for_status()
                return response.json()
            except (httpx.HTTPError, httpx.TimeoutException) as exc:
                last = exc
            if time.monotonic() + delay >= deadline:
                # A timeout does not mean the work did not happen, only that the answer did not arrive. That is why every call carries a key inventory can recognise, and why retrying was safe.
                raise InventoryUnavailable(str(last)) from last
            await asyncio.sleep(delay)
            delay = min(delay * 2, 0.4)     # back off, but keep checking often enough to use the budget

    async def close(self) -> None:
        await self._client.aclose()

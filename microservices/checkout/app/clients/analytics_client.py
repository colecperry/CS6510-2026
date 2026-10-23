# Delivers batches of scans to the analytics service, which owns the data the ranking is built from.
#
# Deliberately thinner than the inventory client. Nothing here retries and nothing here raises: a lost batch costs the ranking a little accuracy, while a scan writer stuck retrying would eventually fill its queue and start dropping scans anyway.
import logging

import httpx

logger = logging.getLogger(__name__)


class AnalyticsClient:
    def __init__(self, base_url: str, timeout_ms: int):
        self._base = base_url.rstrip("/")
        self._client = httpx.AsyncClient(timeout=timeout_ms / 1000)

    async def send_scans(self, skus: list[str]) -> bool:
        """Posts one batch. Returns whether it arrived, and never raises."""
        try:
            response = await self._client.post(
                self._base + "/internal/scans", json={"skus": skus}
            )
            response.raise_for_status()
            return True
        except (httpx.HTTPError, httpx.TimeoutException) as exc:
            # Swallowed on purpose. Analytics being down must not stop people shopping, and the ranking is the one thing the system is allowed to let fall behind.
            logger.warning("could not deliver %d scans to analytics: %s", len(skus), exc)
            return False

    async def close(self) -> None:
        await self._client.aclose()

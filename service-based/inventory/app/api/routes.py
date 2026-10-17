# The two stock endpoints.
#
# Both are reads. Nothing here changes anything.
from fastapi import APIRouter

from app import wiring
from common.schemas import CatalogResponse, LowStockResponse

router = APIRouter()


# Not async, unlike most routes: this one is served from memory and never waits for anything.
@router.get("/items")
def get_items() -> CatalogResponse:
    return wiring.service.list_items()


@router.get("/inventory/low-stock")
async def get_low_stock(threshold: int | None = None) -> LowStockResponse:
    # None means the caller did not pass ?threshold=, so the service uses the configured default.
    return await wiring.service.low_stock(threshold)

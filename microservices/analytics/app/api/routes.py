# The one reporting endpoint.
#
# Reads a ranking that was built earlier, in the background. Returns an empty one rather than an error when nothing has been scanned yet.
from fastapi import APIRouter

from app import wiring
from common.schemas import PopularItemsResponse

router = APIRouter()


@router.get("/analytics/popular-items")
async def get_popular_items(limit: int = 10) -> PopularItemsResponse:
    return await wiring.service.popular_items(limit)   # ?limit=N, default 10

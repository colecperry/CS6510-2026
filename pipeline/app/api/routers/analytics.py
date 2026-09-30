# GET /analytics/popular-items - the current most-scanned ranking.
from fastapi import APIRouter

from app.api import wiring
from app.schemas import PopularItemsResponse

router = APIRouter()


@router.get("/analytics/popular-items")
async def get_popular_items(limit: int = 10) -> PopularItemsResponse:
    return await wiring.analytics.popular_items(limit)

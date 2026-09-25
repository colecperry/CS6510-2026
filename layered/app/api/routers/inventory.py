# GET /inventory/low-stock - which items are running out.
from fastapi import APIRouter

from app.api import wiring
from app.schemas import LowStockResponse

router = APIRouter()


@router.get("/inventory/low-stock")
async def get_low_stock(threshold: int | None = None) -> LowStockResponse:
    return await wiring.analytics.low_stock(threshold)

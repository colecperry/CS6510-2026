# GET /items - the full product list.
#
# The load client fetches this once at startup to learn what barcodes exist.
from fastapi import APIRouter

from app.api import wiring
from app.schemas import CatalogResponse

router = APIRouter()


@router.get("/items")
def get_items() -> CatalogResponse:
    return wiring.transactions.list_items()

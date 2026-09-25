# GET /items - the full product list.
#
# The load client fetches this once at startup to learn what barcodes exist.
from fastapi import APIRouter

import app.catalog_cache as catalog_cache
from app.models import CatalogItem, CatalogResponse

router = APIRouter()


@router.get("/items")
def get_items() -> CatalogResponse:
    """Returns every item in the catalog."""
    # Served from the in-memory cache, so this makes no database call.
    items = [
        CatalogItem(sku=sku, name=name, price=price)
        for sku, (name, price) in catalog_cache.catalog.items()
    ]
    return CatalogResponse(items=items)

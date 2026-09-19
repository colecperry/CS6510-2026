# GET /items - serves the catalog straight from memory, no DB call.
from fastapi import APIRouter

import app.catalog_cache as catalog_cache
from app.models import CatalogItem, CatalogResponse

router = APIRouter()


@router.get("/items")
def get_items() -> CatalogResponse:
    """Returns the full product catalog.

    Takes: nothing.
    Returns: a CatalogResponse listing every item.
    """
    # Build the response straight from the cache - no database call.
    items = [
        CatalogItem(sku=sku, name=name, price=price)
        for sku, (name, price) in catalog_cache.catalog.items()
    ]
    return CatalogResponse(items=items)

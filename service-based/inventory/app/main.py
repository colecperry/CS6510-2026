# Builds the running inventory service.
#
# Opens the database, constructs its two repositories and the service, and mounts the routes. Nothing here knows the other services exist.
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app import wiring
from app.api.routes import router
from app.service import InventoryService
from common.config import settings
from common.data.catalog_repo import CatalogRepo
from common.data.pool import close_pool, create_pool
from common.data.stock_repo import StockRepo
from common.error_handlers import register_error_handlers


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Runs setup before the first request, and cleanup after the last."""
    # A small pool: this service answers twice per load run, while checkout serves everything else.
    pool = await create_pool(settings.inventory_pool_max)

    catalog_repo = CatalogRepo(pool)
    await catalog_repo.load()
    stock_repo = StockRepo(pool)

    wiring.service = InventoryService(
        catalog_repo, stock_repo, settings.low_stock_default_threshold
    )

    yield

    await close_pool(pool)


app = FastAPI(lifespan=lifespan)
register_error_handlers(app)
app.include_router(router)

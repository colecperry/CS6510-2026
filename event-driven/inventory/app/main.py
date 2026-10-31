# Builds the running inventory service.
#
# Opens this service's own database, constructs its two repositories and the service, mounts the routes, and subscribes to sales.
# Nothing here knows the other services exist, and it holds no address or credential for any of them beyond the broker's.
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app import wiring
from app.api.routes import router
from app.bus.consumer import PREFETCH, SalesConsumer
from app.data.catalog_repo import CatalogRepo
from app.data.stock_repo import StockRepo
from app.service import InventoryService
from app.stock_mover import StockMover
from common.bus import connect_with_retry
from common.config import InventorySettings, shared
from common.error_handlers import register_error_handlers
from common.pool import close_pool, create_pool

# This service's own settings. It carries one database URL, its own, so there is no attribute here that reaches another service's store.
settings = InventorySettings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Runs setup before the first request, and cleanup after the last."""
    pool = await create_pool(settings.database_url, settings.pool_max)

    catalog_repo = CatalogRepo(pool)
    await catalog_repo.load()
    stock_repo = StockRepo(pool)

    # The threshold comes from the shared settings because it is contract behaviour every service must agree on, unlike the database URL above.
    wiring.service = InventoryService(
        catalog_repo, stock_repo, shared.low_stock_default_threshold
    )

    # The write path. Last week checkout reached it through an HTTP route on this service; it is now driven entirely by messages, so nothing outside this process can call it.
    stock_mover = StockMover(pool, stock_repo)

    bus = await connect_with_retry(shared.broker_url, PREFETCH)
    consumer = SalesConsumer(bus, stock_mover)
    await consumer.start()

    yield

    await bus.close()
    await close_pool(pool)


app = FastAPI(lifespan=lifespan)
register_error_handlers(app)
app.include_router(router)

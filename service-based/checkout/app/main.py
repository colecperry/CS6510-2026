# Builds the running checkout service.
#
# The only file that knows how this service fits together: it opens the database, constructs the repositories and the service, and mounts the routes. Nothing here knows the other services exist.
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app import wiring
from app.api.routes import router
from app.ingest import ScanWriter
from app.service import CheckoutService
from common.config import settings
from common.data.baskets_repo import BasketsRepo
from common.data.catalog_repo import CatalogRepo
from common.data.pool import close_pool, create_pool
from common.data.scans_repo import ScansRepo
from common.error_handlers import register_error_handlers


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Runs setup before the first request, and cleanup after the last."""
    # This service gets its own pool, sized for the traffic it actually sees. It serves almost every request, so it takes most of the database's allowance.
    pool = await create_pool(settings.checkout_pool_max)

    catalog_repo = CatalogRepo(pool)
    await catalog_repo.load()
    baskets_repo = BasketsRepo(pool)
    scans_repo = ScansRepo(pool)

    # Carries scans out of the request and into the shared log, where the reporting service will find them. Started before any request arrives.
    scan_writer = ScanWriter(scans_repo)
    await scan_writer.start()

    wiring.service = CheckoutService(baskets_repo, catalog_repo, scan_writer)

    yield

    # Drain before the database goes away, or queued scans are lost on every restart.
    await scan_writer.stop()
    await close_pool(pool)


app = FastAPI(lifespan=lifespan)
register_error_handlers(app)
app.include_router(router)

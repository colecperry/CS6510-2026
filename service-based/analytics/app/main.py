# Builds the running analytics service.
#
# Opens the database, constructs the service, mounts the one route, and starts the background rebuild. The only service here that does work nobody asked for.
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app import wiring
from app.api.routes import router
from app.rebuilder import RankingRebuilder
from app.service import AnalyticsService
from common.config import settings
from common.data.catalog_repo import CatalogRepo
from common.data.pool import close_pool, create_pool
from common.data.scans_repo import ScansRepo
from common.error_handlers import register_error_handlers


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Runs setup before the first request, and cleanup after the last."""
    # A small pool: this service answers once per load run, plus the background rebuild's occasional poll.
    pool = await create_pool(settings.analytics_pool_max)

    scans_repo = ScansRepo(pool)
    wiring.service = AnalyticsService(scans_repo)

    # Item names for the ranking come from this service's own copy of the catalogue, so it never has to ask another service what something is called.
    catalog_repo = CatalogRepo(pool)
    await catalog_repo.load()

    # The only work in this system that nobody asked for. It polls the shared database because the checkout service cannot tell it a scan happened.
    rebuilder = RankingRebuilder(scans_repo, catalog_repo, settings.popularity_snapshot_cap)
    await rebuilder.start()

    yield

    await rebuilder.stop()
    await close_pool(pool)


app = FastAPI(lifespan=lifespan)
register_error_handlers(app)
app.include_router(router)

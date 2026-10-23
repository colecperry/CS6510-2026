# Builds the running analytics service.
#
# Opens this service's own database, constructs the service, mounts the one route, and starts the background rebuild.
# The only service here that does work nobody asked for, because nothing tells it when a scan happened.
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app import wiring
from app.api.internal_routes import router as internal_router
from app.api.routes import router
from app.data.catalog_repo import CatalogRepo
from app.data.ranking_repo import RankingRepo
from app.data.scans_repo import ScansRepo
from app.rebuilder import RankingRebuilder
from app.service import AnalyticsService
from common.config import AnalyticsSettings, shared
from common.error_handlers import register_error_handlers
from common.pool import close_pool, create_pool

# This service's own settings. One database URL, its own, and no address for any other service.
settings = AnalyticsSettings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Runs setup before the first request, and cleanup after the last."""
    pool = await create_pool(settings.database_url, settings.pool_max)

    scans_repo = ScansRepo(pool)
    ranking_repo = RankingRepo(pool)
    wiring.service = AnalyticsService(ranking_repo)
    # Checkout posts batches of scans here, because it cannot write to this service's database.
    wiring.scans = scans_repo

    # Item names for the ranking come from this service's own copy of the catalogue, so it never has to ask another service what something is called.
    catalog_repo = CatalogRepo(pool)
    await catalog_repo.load()

    # The only work in this system that nobody asked for. It polls because checkout posts scans and then forgets them, so nothing announces that a rebuild is due.
    rebuilder = RankingRebuilder(
        scans_repo, ranking_repo, catalog_repo, shared.popularity_snapshot_cap
    )
    await rebuilder.start()

    yield

    await rebuilder.stop()
    await close_pool(pool)


app = FastAPI(lifespan=lifespan)
register_error_handlers(app)
app.include_router(router)
# Mounted on this service but not on the gateway, so only another service inside the system can reach it.
app.include_router(internal_router)

# Builds the running checkout service.
#
# The only file that knows how this service fits together: it opens the database, constructs the repositories and the service, and mounts the routes. Nothing here knows the other services exist.
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app import wiring
from app.api.routes import router
from app.clients.analytics_client import AnalyticsClient
from app.clients.inventory_client import InventoryClient
from app.data.baskets_repo import BasketsRepo
from app.data.catalog_repo import CatalogRepo
from app.data.saga_repo import SagaRepo
from app.ingest import ScanWriter
from app.saga.coordinator import PaymentCoordinator
from app.saga.recovery import SagaRecovery
from app.service import CheckoutService
from common.config import CheckoutSettings
from common.error_handlers import register_error_handlers
from common.pool import close_pool, create_pool

# This service's own settings. It is the only one holding another service's address, because paying has to reduce stock that inventory owns.
settings = CheckoutSettings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Runs setup before the first request, and cleanup after the last."""
    # Checkout serves almost every request, so it gets much the largest pool of the three.
    pool = await create_pool(settings.database_url, settings.pool_max)

    catalog_repo = CatalogRepo(pool)
    await catalog_repo.load()
    baskets_repo = BasketsRepo(pool)

    # The one outbound dependency in the system: stock lives in inventory's database, so paying has to ask for it.
    inventory_client = InventoryClient(
        settings.inventory_url,
        settings.stock_call_timeout_ms,
        settings.stock_retry_budget_ms,
    )
    saga_repo = SagaRepo(pool)
    payments = PaymentCoordinator(baskets_repo, saga_repo, inventory_client)

    # Repair anything a previous run left half done, before this one takes a single request. Serving first could let a shopper scan into a basket whose stock has already been taken.
    await SagaRecovery(baskets_repo, saga_repo, inventory_client).recover_once()

    # Scans belong to analytics, so they leave this process over the network. The writer queues them so a shopper never waits for that.
    analytics_client = AnalyticsClient(settings.analytics_url, settings.stock_call_timeout_ms)
    scan_writer = ScanWriter(analytics_client)
    await scan_writer.start()

    wiring.service = CheckoutService(
        baskets_repo, catalog_repo, scans=scan_writer, payments=payments
    )

    yield

    # Deliver queued scans before the connection goes away, or they are lost on every restart.
    await scan_writer.stop()
    await analytics_client.close()
    await inventory_client.close()
    await close_pool(pool)


app = FastAPI(lifespan=lifespan)
register_error_handlers(app)
app.include_router(router)

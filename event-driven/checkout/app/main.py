# Builds the running checkout service.
#
# The only file that knows how this service fits together: it opens the database, constructs the repositories and the service, mounts the routes, and starts the two background tasks that carry work out of this process.
# Nothing here knows the other services exist. It holds one address beyond its own database, the broker's, and the broker is not a service.
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app import wiring
from app.api.routes import router
from app.bus.publisher import CheckoutPublisher
from app.data.baskets_repo import BasketsRepo
from app.data.catalog_repo import CatalogRepo
from app.data.outbox_repo import OutboxRepo
from app.ingest import ScanWriter
from app.outbox import OutboxRelay
from app.service import CheckoutService
from common.bus import connect_with_retry
from common.config import CheckoutSettings, shared
from common.error_handlers import register_error_handlers
from common.pool import close_pool, create_pool

# This service's own settings. It now holds no other service's address, because it calls none of them.
settings = CheckoutSettings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Runs setup before the first request, and cleanup after the last."""
    # Checkout serves almost every request, so it gets much the largest pool of the three.
    pool = await create_pool(settings.database_url, settings.pool_max)

    catalog_repo = CatalogRepo(pool)
    await catalog_repo.load()
    baskets_repo = BasketsRepo(pool)
    outbox_repo = OutboxRepo(pool)

    # Nothing is repaired at startup any more. The saga recovery that used to run here existed because a payment could die between reducing stock and marking the basket paid; a payment is now one transaction, so there is no half-finished state to find.

    # The one connection this service has to anything outside its own database. It waits for the broker rather than dying, because the broker takes several seconds longer to accept connections than Postgres does.
    bus = await connect_with_retry(shared.broker_url)
    publisher = CheckoutPublisher(bus)

    # Both of these take work out of the request path, and for different reasons. Scans are queued in memory because they are allowed to be lost; sales are queued in the database because they are not.
    scan_writer = ScanWriter(publisher)
    await scan_writer.start()

    relay = OutboxRelay(outbox_repo, publisher, settings.outbox_poll_ms)
    await relay.start()

    wiring.service = CheckoutService(
        pool, baskets_repo, catalog_repo, outbox_repo, scans=scan_writer
    )

    yield

    # Deliver queued scans before the connection goes away, or they are lost on every restart.
    await scan_writer.stop()
    await relay.stop()
    await bus.close()
    await close_pool(pool)


app = FastAPI(lifespan=lifespan)
register_error_handlers(app)
app.include_router(router)

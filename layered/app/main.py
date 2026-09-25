# Builds the running application by assembling the four layers.
#
# This is the only file that knows how the layers fit together: it opens the
# database, constructs each repository, hands those to the services, and
# mounts the routes on top. Everything else just uses what it is given.
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.analytics.service import AnalyticsService
from app.api import wiring
from app.api.error_handlers import register_error_handlers
from app.api.routers import analytics, catalog, inventory, transactions
from app.config import settings
from app.data.analytics_repo import AnalyticsRepo
from app.data.catalog_repo import CatalogRepo
from app.data.pool import close_pool, create_pool
from app.data.transactions_repo import TransactionsRepo
from app.transactions.service import TransactionService


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Runs setup before the first request, and cleanup after the last."""
    # Everything before `yield` happens at startup, everything after it at
    # shutdown.
    pool = await create_pool()

    # Bottom layer first: the repositories, each handed the pool.
    catalog_repo = CatalogRepo(pool)
    await catalog_repo.load()
    transactions_repo = TransactionsRepo(pool)
    analytics_repo = AnalyticsRepo(pool)

    # Then the services, each handed the repositories it needs. Transactions
    # depends on analytics because every scan feeds the popularity window.
    analytics_service = AnalyticsService(
        analytics_repo,
        catalog_repo,
        settings.low_stock_default_threshold,
        settings.popularity_snapshot_cap,
    )
    transaction_service = TransactionService(
        transactions_repo, catalog_repo, analytics_service
    )

    # Built once, here, so no request ever pays to look them up.
    wiring.transactions = transaction_service
    wiring.analytics = analytics_service

    yield

    await close_pool(pool)


app = FastAPI(lifespan=lifespan)
register_error_handlers(app)

app.include_router(catalog.router)
app.include_router(transactions.router)
app.include_router(inventory.router)
app.include_router(analytics.router)

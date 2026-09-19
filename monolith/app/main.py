# Wires everything else together into one running app.
from contextlib import asynccontextmanager

from fastapi import FastAPI

import app.catalog_cache as catalog_cache
import app.pool as pool_module
from app.errors import register_error_handlers
from app.routers import analytics, catalog, inventory, transactions


# Runs once at startup (before yield) and once at shutdown (after yield).
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Runs setup before the app serves requests, and cleanup after it stops.

    Takes: the FastAPI app instance (required by FastAPI, unused here).
    Returns: nothing - yields control back to FastAPI while the app runs.
    """
    await pool_module.connect()
    await catalog_cache.load()
    yield
    await pool_module.disconnect()


app = FastAPI(lifespan=lifespan)
register_error_handlers(app)

# One router per group of endpoints, from app/routers/.
app.include_router(catalog.router)
app.include_router(transactions.router)
app.include_router(inventory.router)
app.include_router(analytics.router)

# Builds the running application: opens the database, fills the catalog
# cache, and mounts every route.
#
# app/serve.py imports `app` from here and hands it to the web server.
from contextlib import asynccontextmanager

from fastapi import FastAPI

import app.catalog_cache as catalog_cache
import app.pool as pool_module
from app.errors import register_error_handlers
from app.routers import analytics, catalog, inventory, transactions


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Runs setup before the first request, and cleanup after the last."""
    # Everything before `yield` happens at startup, everything after it at
    # shutdown. The catalog load needs the pool, so order matters here.
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

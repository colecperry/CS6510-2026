# Builds the running gateway.
#
# Much shorter than the services' equivalents, and that is the point: no database, no repositories, no service object. There is nothing to assemble because the gateway holds nothing.
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.routes import client, router


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Runs setup before the first request, and cleanup after the last."""
    yield
    # The one thing worth tidying: the shared connection to the services.
    await client.aclose()


app = FastAPI(lifespan=lifespan)
app.include_router(router)

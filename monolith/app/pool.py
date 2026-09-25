# The shared set of database connections.
#
# main.py opens it at startup and closes it at shutdown. Every other module
# reaches for the `pool` global directly when it needs to run a query.
import asyncpg

from app.config import settings

pool: asyncpg.Pool | None = None


async def connect() -> None:
    """Opens the pool. Call once, at startup."""
    global pool
    # 120 covers stress mode's 100 stations plus the popularity recompute's
    # occasional extra connection. docker-compose raises Postgres's own
    # limit to 200 so it can accept them.
    pool = await asyncpg.create_pool(settings.database_url, min_size=10, max_size=120)


async def disconnect() -> None:
    """Closes every connection. Call once, at shutdown."""
    global pool
    if pool is not None:
        await pool.close()
        pool = None


# Written as a FastAPI dependency, but nothing uses it - the routers all read
# the `pool` global instead.
async def get_conn():
    """Lends one connection for the length of a single request."""
    async with pool.acquire() as conn:
        yield conn

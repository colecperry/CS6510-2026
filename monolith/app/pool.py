import asyncpg

from app.config import settings

# Shared across the whole app - created once at startup, reused per request.
pool: asyncpg.Pool | None = None


# Opens the pool - called once, at app startup.
async def connect() -> None:
    """Opens the shared connection pool.

    Takes: nothing.
    Returns: nothing - sets the module-level `pool` variable.
    """
    global pool
    # Default max_size (10) is too small for stress mode (up to 100 stations)
    # plus the popularity recompute path's occasional extra connection.
    pool = await asyncpg.create_pool(settings.database_url, min_size=10, max_size=120)


# Closes every connection - called once, at app shutdown.
async def disconnect() -> None:
    """Closes every connection in the pool.

    Takes: nothing.
    Returns: nothing.
    """
    global pool
    if pool is not None:
        await pool.close()
        pool = None


# Borrows one connection from the pool, then returns it automatically.
async def get_conn():
    """FastAPI dependency that lends one connection for a single request.

    Takes: nothing.
    Returns: a connection, automatically released when the request ends.
    """
    async with pool.acquire() as conn:
        yield conn
# Opens and closes one service's set of database connections.
#
# The URL is passed in rather than read from settings here, because each service now has its own database and must be given only its own address. A shared helper that knew where to connect would be a way for any service to reach any store.
import asyncpg


async def create_pool(url: str, max_size: int) -> asyncpg.Pool:
    """Creates the connection pool. Call once, at startup, with this service's own database URL."""
    return await asyncpg.create_pool(url, min_size=2, max_size=max_size)


async def close_pool(pool: asyncpg.Pool) -> None:
    """Closes every connection. Call once, at shutdown."""
    await pool.close()

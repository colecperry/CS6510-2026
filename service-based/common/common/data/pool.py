# Opens and closes a service's set of database connections.
#
# Each service calls create_pool() once at startup with its own size, because three processes now share one database server rather than one process having it to itself.
import asyncpg

from common.config import settings


async def create_pool(max_size: int) -> asyncpg.Pool:
    """Creates the connection pool. Call once, at startup."""
    # Size is passed in rather than fixed here: checkout serves almost all the traffic, while the other two answer once per run. See .env.example.
    return await asyncpg.create_pool(settings.database_url, min_size=2, max_size=max_size)


async def close_pool(pool: asyncpg.Pool) -> None:
    """Closes every connection. Call once, at shutdown."""
    await pool.close()

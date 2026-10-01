# Opens and closes the shared set of database connections.
#
# main.py calls create_pool() once at startup and hands the result to every
# repository. Repositories never open connections of their own.
import asyncpg

from app.config import settings


async def create_pool() -> asyncpg.Pool:
    """Creates the connection pool. Call once, at startup."""
    # A pool keeps connections open and lends them out, because opening one
    # per query would be far slower than the query itself.
    return await asyncpg.create_pool(
        settings.database_url,
        min_size=10,
        max_size=120,   # enough for 100 checkouts at once, plus headroom
    )


async def close_pool(pool: asyncpg.Pool) -> None:
    """Closes every connection. Call once, at shutdown."""
    await pool.close()

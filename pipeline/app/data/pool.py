# Opens and closes the shared set of database connections.
#
# main.py calls create_pool() once at startup and hands the result to every
# repository. Repositories never open connections of their own.
import asyncpg

from app.config import settings


async def create_pool() -> asyncpg.Pool:
    """Creates the connection pool. Call once, at startup."""
    # 120 covers stress mode's 100 stations plus the popularity recompute's
    # occasional extra connection. docker-compose raises Postgres's own
    # limit to 200 so it can accept them.
    return await asyncpg.create_pool(settings.database_url, min_size=10, max_size=120)


async def close_pool(pool: asyncpg.Pool) -> None:
    """Closes every connection. Call once, at shutdown."""
    await pool.close()

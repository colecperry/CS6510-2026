# Proves that applying the same stock change twice only moves stock once.
#
# This one talks to the real database on purpose. The guarantee comes from a Postgres ON CONFLICT clause, so a stand-in would only be testing the stand-in. Skipped when the database is not running, so the rest of the suite still works without Docker.
import asyncio

import asyncpg
import pytest
from app.data.stock_repo import StockRepo
from app.stock_mover import StockMover
from common.config import InventorySettings

SKU = "SKU-000001"
pytestmark = pytest.mark.asyncio


async def _pool():
    try:
        return await asyncpg.create_pool(
            InventorySettings().database_url, min_size=1, max_size=4, timeout=2
        )
    except (OSError, asyncpg.PostgresError, asyncio.TimeoutError):
        pytest.skip("inventory database is not running")


async def _qty(pool) -> int:
    return await pool.fetchval("SELECT qty FROM stock WHERE sku = $1", SKU)


@pytest.fixture
async def mover():
    pool = await _pool()
    # Each test uses a key of its own and puts the stock back, so running the suite twice leaves the database as it found it.
    yield StockMover(pool, StockRepo(pool)), pool
    await pool.close()


async def test_the_same_key_twice_moves_stock_once(mover):
    stock_mover, pool = mover
    key = "test-idempotency-once"
    before = await _qty(pool)

    first = await stock_mover.reduce(key, [(SKU, 3)])
    after_first = await _qty(pool)
    second = await stock_mover.reduce(key, [(SKU, 3)])
    after_second = await _qty(pool)

    assert first is True and second is False      # the second call reports it changed nothing
    assert after_first == before - 3
    assert after_second == after_first             # and it really did change nothing

    await stock_mover.restore(key)


async def test_concurrent_copies_of_one_request_still_move_stock_once(mover):
    # A retry can overlap the original rather than following it. If the check and the insert were separate statements, both copies could pass the check and stock would go down twice.
    stock_mover, pool = mover
    key = "test-idempotency-concurrent"
    before = await _qty(pool)

    results = await asyncio.gather(*[stock_mover.reduce(key, [(SKU, 2)]) for _ in range(5)])

    assert sum(results) == 1                       # exactly one of the five did the work
    assert await _qty(pool) == before - 2

    await stock_mover.restore(key)


async def test_an_undo_only_puts_stock_back_once(mover):
    stock_mover, pool = mover
    key = "test-idempotency-undo"
    before = await _qty(pool)

    await stock_mover.reduce(key, [(SKU, 4)])
    first = await stock_mover.restore(key)
    second = await stock_mover.restore(key)

    assert first is True and second is False
    assert await _qty(pool) == before              # back exactly where it started, not above it


async def test_undoing_something_never_applied_changes_nothing(mover):
    # Compensation sends this when it does not know whether the original landed, so it has to be harmless.
    stock_mover, pool = mover
    before = await _qty(pool)

    assert await stock_mover.restore("test-key-that-was-never-applied") is False
    assert await _qty(pool) == before

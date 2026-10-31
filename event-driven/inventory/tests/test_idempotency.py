# Proves that applying the same sale twice only moves stock once.
#
# This one talks to the real database on purpose. The guarantee comes from a Postgres ON CONFLICT clause, so a stand-in would only be testing the stand-in. Skipped when the database is not running, so the rest of the suite still works without Docker.
#
# The key is now the message id carried on the sale, because a repeat is a message the broker delivered twice rather than a request checkout retried.
import asyncio
import uuid

import asyncpg
import pytest
from app.data.stock_repo import StockRepo
from app.stock_mover import StockMover
from common.config import InventorySettings

pytestmark = pytest.mark.asyncio

# How much stock the chosen item needs before a test touches it. A load run can empty a popular item completely, and apply_delta floors at zero, so a test pinned to a sold-out SKU would measure a reduction that could not happen.
NEEDED = 100


async def _pool():
    try:
        return await asyncpg.create_pool(
            InventorySettings().database_url, min_size=1, max_size=4, timeout=2
        )
    except (OSError, asyncpg.PostgresError, asyncio.TimeoutError):
        pytest.skip("inventory database is not running")


async def _qty(pool, sku: str) -> int:
    return await pool.fetchval("SELECT qty FROM stock WHERE sku = $1", sku)


@pytest.fixture
async def mover():
    pool = await _pool()
    # Ids are generated per run, because applying one leaves a row behind for ever. A fixed id would pass once and then fail on every later run.
    sku = await pool.fetchval("SELECT sku FROM stock WHERE qty >= $1 ORDER BY sku LIMIT 1", NEEDED)
    if sku is None:
        pytest.skip(f"no item has {NEEDED} units left; run ./scripts/reset_db.sh")
    yield StockMover(pool, StockRepo(pool)), pool, sku
    await pool.close()


async def test_the_same_message_twice_moves_stock_once(mover):
    stock_mover, pool, sku = mover
    message_id = uuid.uuid4()
    before = await _qty(pool, sku)

    first = await stock_mover.reduce(message_id, [(sku, 3)])
    after_first = await _qty(pool, sku)
    second = await stock_mover.reduce(message_id, [(sku, 3)])
    after_second = await _qty(pool, sku)

    assert first is True and second is False      # the second call reports it changed nothing
    assert after_first == before - 3
    assert after_second == after_first             # and it really did change nothing


async def test_concurrent_copies_of_one_sale_still_move_stock_once(mover):
    # A redelivery can overlap the original rather than following it. If the check and the insert were separate statements, both copies could pass the check and stock would go down twice.
    stock_mover, pool, sku = mover
    message_id = uuid.uuid4()
    before = await _qty(pool, sku)

    results = await asyncio.gather(
        *[stock_mover.reduce(message_id, [(sku, 2)]) for _ in range(5)]
    )

    assert sum(results) == 1                       # exactly one of the five did the work
    assert await _qty(pool, sku) == before - 2


async def test_two_different_sales_both_move_stock(mover):
    # The other half of the guarantee. Deduplicating is only correct if it deduplicates nothing else, and two shoppers buying the same item must both be charged for it.
    stock_mover, pool, sku = mover
    before = await _qty(pool, sku)

    assert await stock_mover.reduce(uuid.uuid4(), [(sku, 1)]) is True
    assert await stock_mover.reduce(uuid.uuid4(), [(sku, 1)]) is True

    assert await _qty(pool, sku) == before - 2

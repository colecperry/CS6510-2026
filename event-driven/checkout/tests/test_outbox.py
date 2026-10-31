# Proves the one guarantee the outbox exists for: a basket is never marked paid without the message that reduces its stock.
#
# This talks to the real database, because the guarantee is a Postgres transaction and a stand-in would only be testing the stand-in. Skipped when the database is not running.
import asyncio
import uuid

import asyncpg
import pytest
from app.data.baskets_repo import BasketsRepo
from app.data.catalog_repo import CatalogRepo
from app.data.outbox_repo import OutboxRepo
from app.service import CheckoutService
from common.config import CheckoutSettings
from common.errors import ConflictError

SKU = "SKU-000001"
pytestmark = pytest.mark.asyncio


class FakeScanSink:
    def record(self, sku):
        pass


class BrokenOutboxRepo(OutboxRepo):
    """An outbox whose write always fails, standing in for a disk error or a constraint violation inside the payment transaction."""

    async def enqueue(self, conn, message_id, routing_key, payload):
        raise RuntimeError("the outbox write failed")


@pytest.fixture
async def checkout():
    try:
        pool = await asyncpg.create_pool(
            CheckoutSettings().database_url, min_size=1, max_size=4, timeout=2
        )
    except (OSError, asyncpg.PostgresError, asyncio.TimeoutError):
        pytest.skip("checkout database is not running")

    catalog = CatalogRepo(pool)
    await catalog.load()
    baskets = BasketsRepo(pool)
    yield pool, baskets, catalog
    await pool.close()


def build(pool, baskets, catalog, outbox_repo):
    return CheckoutService(pool, baskets, catalog, outbox_repo, FakeScanSink())


async def _basket_with_one_item(service):
    started = await service.start(f"test-station-{uuid.uuid4().hex[:8]}")
    await service.scan(started.transaction_id, SKU)
    return started.transaction_id


async def test_paying_writes_the_basket_and_the_message_together(checkout):
    pool, baskets, catalog = checkout
    service = build(pool, baskets, catalog, OutboxRepo(pool))
    transaction_id = await _basket_with_one_item(service)

    receipt = await service.complete(transaction_id)

    status = await pool.fetchval(
        "SELECT status FROM transactions WHERE transaction_id = $1", transaction_id
    )
    row = await pool.fetchrow(
        "SELECT routing_key, payload FROM outbox WHERE payload->>'transactionId' = $1",
        transaction_id,
    )

    assert receipt.item_count == 1
    assert status == "COMPLETED"
    assert row is not None, "the basket was marked paid with nothing to reduce its stock"
    assert row["routing_key"] == "sale.completed"


async def test_a_failed_outbox_write_leaves_the_basket_unpaid(checkout):
    # The half the transaction is there to prevent. If these two could land separately, this is the case where the shopper is charged and the stock never moves.
    pool, baskets, catalog = checkout
    service = build(pool, baskets, catalog, BrokenOutboxRepo(pool))
    transaction_id = await _basket_with_one_item(service)

    with pytest.raises(RuntimeError):
        await service.complete(transaction_id)

    status = await pool.fetchval(
        "SELECT status FROM transactions WHERE transaction_id = $1", transaction_id
    )
    owed = await pool.fetchval(
        "SELECT count(*) FROM outbox WHERE payload->>'transactionId' = $1", transaction_id
    )

    assert status == "OPEN", "the basket was marked paid although the message was lost"
    assert owed == 0


async def test_a_message_is_owed_before_anything_is_sent(checkout):
    # Nothing in the payment path touches the broker, which is what lets a shopper pay while it is down. The row sitting unsent is the evidence.
    pool, baskets, catalog = checkout
    service = build(pool, baskets, catalog, OutboxRepo(pool))
    transaction_id = await _basket_with_one_item(service)

    await service.complete(transaction_id)

    sent_at = await pool.fetchval(
        "SELECT sent_at FROM outbox WHERE payload->>'transactionId' = $1", transaction_id
    )
    assert sent_at is None


async def test_paying_twice_is_refused_and_owes_only_one_message(checkout):
    pool, baskets, catalog = checkout
    service = build(pool, baskets, catalog, OutboxRepo(pool))
    transaction_id = await _basket_with_one_item(service)

    await service.complete(transaction_id)
    with pytest.raises(ConflictError) as caught:
        await service.complete(transaction_id)

    owed = await pool.fetchval(
        "SELECT count(*) FROM outbox WHERE payload->>'transactionId' = $1", transaction_id
    )
    assert caught.value.error == "TRANSACTION_NOT_OPEN"
    assert owed == 1, "a refused payment still announced a sale"


async def test_an_empty_basket_is_refused_and_owes_nothing(checkout):
    pool, baskets, catalog = checkout
    service = build(pool, baskets, catalog, OutboxRepo(pool))
    started = await service.start("test-station-empty")

    with pytest.raises(ConflictError) as caught:
        await service.complete(started.transaction_id)

    owed = await pool.fetchval(
        "SELECT count(*) FROM outbox WHERE payload->>'transactionId' = $1",
        started.transaction_id,
    )
    assert caught.value.error == "EMPTY_BASKET"
    assert owed == 0

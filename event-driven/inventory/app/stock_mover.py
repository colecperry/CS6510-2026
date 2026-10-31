# The only way stock changes, driven by a message saying a sale happened.
#
# Separate from InventoryService, which only reads. Its whole job is to make a repeated message harmless: recording the message id and moving the stock happen in one transaction, so an id is never recorded without the stock moving, and stock never moves twice for one id.
#
# The applied_requests SQL lives here rather than in its own repository because those rows and the stock rows are only ever written together. Splitting them across two files would hide the fact that they share a transaction.
import json
from uuid import UUID

import asyncpg

from app.data.stock_repo import StockRepo


class StockMover:
    def __init__(self, pool: asyncpg.Pool, stock: StockRepo) -> None:
        self._pool = pool
        self._stock = stock

    async def reduce(self, message_id: UUID, lines: list[tuple[str, int]]) -> bool:
        """Reduces stock for one sale. Returns False if this message was seen before, so stock is only reduced once."""
        async with self._pool.acquire() as conn: # borrow a connection to the pool
            async with conn.transaction(): # start transaction: id & stock change happen together
                if not await self._claim(conn, message_id, lines): # try to record the id
                    return False         # a repeat; the first copy already did the work
                await self._stock.apply_delta(conn, lines, sign=-1) # reduce the stock
                return True

    async def _claim(
        self, conn: asyncpg.Connection, message_id: UUID, lines: list[tuple[str, int]]
    ) -> bool:
        """Tries to add the id to the list of messages this service has already acted on. True if it was added, False if it was already there."""
        # The insert is the check. Doing it as a separate read first would leave a window in which two copies both found nothing and both reduced stock.
        row = await conn.fetchrow(
            """
            INSERT INTO applied_requests (message_id, lines)
            VALUES ($1, $2::jsonb)
            ON CONFLICT (message_id) DO NOTHING
            RETURNING message_id
            """,
            message_id,
            json.dumps([{"sku": sku, "qty": qty} for sku, qty in lines]),
        )
        return row is not None # Got an id back: this copy is the first, so reduce. Got None: already applied, do nothing.

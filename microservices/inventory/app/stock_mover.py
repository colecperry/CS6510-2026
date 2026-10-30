# The only way stock changes, used by the checkout service during a payment.
#
# Separate from InventoryService, which only reads. Its whole job is to make a repeated request harmless: recording the caller's key and moving the stock happen in one transaction, so a key is never recorded without the stock moving, and stock never moves twice for one key.
#
# The applied_requests SQL lives here rather than in its own repository because those rows and the stock rows are only ever written together. Splitting them across two files would hide the fact that they share a transaction.
import json

import asyncpg

from app.data.stock_repo import StockRepo


class StockMover:
    def __init__(self, pool: asyncpg.Pool, stock: StockRepo):
        self._pool = pool
        self._stock = stock

    async def reduce(self, key: str, lines: list[tuple[str, int]]) -> bool:
        """Reduces stock for one request. Returns False if this key was seen before, so stock is only reduced once."""
        async with self._pool.acquire() as conn: # borrow a connection to the pool
            async with conn.transaction(): # start transaction: key & stock change happen together
                if not await self._claim(conn, key, lines): # try to record the key
                    return False         # a repeat; the first copy already did the work
                await self._stock.apply_delta(conn, lines, sign=-1) # reduce the stock
                return True

    async def restore(self, key: str) -> bool:
        """Puts back exactly what the named request took. False if it was never applied or is already undone."""
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                lines = await self._claim_restore(conn, key)
                if lines is None:
                    return False
                await self._stock.apply_delta(conn, lines, sign=+1)
                return True

    async def was_applied(self, key: str) -> bool:
        """Whether this key is currently applied. Asked by checkout's recovery when it does not know how a payment ended."""
        return await self._pool.fetchval(
            """
            SELECT EXISTS (
                SELECT 1 FROM applied_requests
                WHERE idempotency_key = $1 AND restored_at IS NULL
            )
            """,
            key,
        )

    async def _claim(
        self, conn: asyncpg.Connection, key: str, lines: list[tuple[str, int]]
    ) -> bool:
        """Tries to add the key to inventory's list of keys it has seen before. Returns True if it was added, False if it was already there. The caller must then reduce stock or not."""
        # Add a row to applied_requests - inventory's list of keys it has seen before
        # If key already in the table don't add it again
        row = await conn.fetchrow(
            """
            INSERT INTO applied_requests (idempotency_key, lines) 
            VALUES ($1, $2::jsonb)
            ON CONFLICT (idempotency_key) DO NOTHING
            RETURNING idempotency_key
            """,
            key,
            json.dumps([{"sku": sku, "qty": qty} for sku, qty in lines]),
        )
        return row is not None # Got a key back - row was added, got a None back - row was already there, don't reduce stock

    async def _claim_restore(
        self, conn: asyncpg.Connection, key: str
    ) -> list[tuple[str, int]] | None:
        """Takes hold of an applied request so it can be undone. None if it was never applied or is already undone."""
        # Both conditions sit in the WHERE, so two undo requests for the same key cannot both succeed and put the stock back twice.
        row = await conn.fetchrow(
            """
            UPDATE applied_requests
            SET restored_at = now()
            WHERE idempotency_key = $1 AND restored_at IS NULL
            RETURNING lines
            """,
            key,
        )
        if row is None:
            return None
        return [(line["sku"], line["qty"]) for line in json.loads(row["lines"])]

# The record of how far a payment got, and the statements that move it along.
#
# Paying changes two databases: this one marks the basket paid, inventory's reduces stock. No transaction can cover both, so each payment writes a row here first saying what it intends to do.
# Without that row, a crash between the two halves would be unrecoverable: nothing afterwards could tell a payment that never started from one that got half way.
import json
from datetime import datetime
from typing import NamedTuple

import asyncpg


# A payment that was interrupted and has to be finished or undone.
class UnfinishedSaga(NamedTuple):
    saga_id: str
    transaction_id: str
    state: str
    lines: list[tuple[str, int]]
    created_at: datetime


class SagaRepo:
    def __init__(self, pool: asyncpg.Pool):
        self._pool = pool

    def acquire(self):
        """Hands out a connection so the caller can claim a basket and open its saga in one transaction."""
        return self._pool.acquire()

    async def claim_basket(
        self, conn: asyncpg.Connection, transaction_id: str
    ) -> bool:
        """Takes exclusive hold of a basket for payment. False if it was not open, was empty, or is already being paid for."""
        # One statement does all the guarding: open, not empty, and no payment already running. Two simultaneous payments both run this and exactly one gets a row back, so the other is refused rather than both proceeding.
        row = await conn.fetchrow(
            """
            UPDATE transactions
            SET paying_since = now()
            WHERE transaction_id = $1
              AND status = 'OPEN'
              AND paying_since IS NULL
              AND item_count > 0
            RETURNING transaction_id
            """,
            transaction_id,
        )
        return row is not None

    async def open_saga(
        self,
        conn: asyncpg.Connection,
        saga_id: str,
        transaction_id: str,
        lines: list[tuple[str, int]],
    ) -> None:
        """Records what this payment intends to reduce, before anything is asked of inventory."""
        # The quantities are frozen here rather than read again later, so compensation puts back exactly what was taken even if the basket changed afterwards.
        await conn.execute(
            """
            INSERT INTO payment_saga (saga_id, transaction_id, state, lines)
            VALUES ($1, $2, 'PENDING', $3::jsonb)
            """,
            saga_id,
            transaction_id,
            json.dumps([{"sku": sku, "qty": qty} for sku, qty in lines]),
        )

    async def mark(self, saga_id: str, state: str) -> None:
        """Moves a saga to its next state. The four are PENDING, APPLIED, DONE and COMPENSATED."""
        await self._pool.execute(
            "UPDATE payment_saga SET state = $2, updated_at = now() WHERE saga_id = $1",
            saga_id,
            state,
        )

    async def count_attempt(self, saga_id: str) -> None:
        """Records one more try at the stock call. Telemetry only; nothing branches on it."""
        await self._pool.execute(
            "UPDATE payment_saga SET attempts = attempts + 1 WHERE saga_id = $1", saga_id
        )

    async def finish(
        self, conn: asyncpg.Connection, transaction_id: str, saga_id: str
    ) -> asyncpg.Record | None:
        """Marks the basket paid and the saga done, in one transaction. None if the basket was no longer claimable."""
        row = await conn.fetchrow(
            """
            UPDATE transactions
            SET status = 'COMPLETED', completed_at = now(), paying_since = NULL
            WHERE transaction_id = $1 AND status = 'OPEN' AND paying_since IS NOT NULL
            RETURNING station_id, item_count, running_total, started_at, completed_at
            """,
            transaction_id,
        )
        if row is None:
            return None
        await conn.execute(
            "UPDATE payment_saga SET state = 'DONE', updated_at = now() WHERE saga_id = $1",
            saga_id,
        )
        return row

    async def release_basket(self, transaction_id: str, saga_id: str) -> None:
        """Gives an unpaid basket back to the shopper and closes the saga as compensated."""
        # Clearing paying_since makes the basket scannable again, so a refused payment leaves it exactly as it was before the attempt.
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    "UPDATE transactions SET paying_since = NULL WHERE transaction_id = $1",
                    transaction_id,
                )
                await conn.execute(
                    "UPDATE payment_saga SET state = 'COMPENSATED', updated_at = now() WHERE saga_id = $1",
                    saga_id,
                )

    async def unfinished(self) -> list[UnfinishedSaga]:
        """Every payment left part way, for recovery to finish or undo at startup."""
        rows = await self._pool.fetch(
            """
            SELECT saga_id, transaction_id, state, lines, created_at
            FROM payment_saga
            WHERE state IN ('PENDING', 'APPLIED')
            ORDER BY created_at
            """
        )
        return [
            UnfinishedSaga(
                saga_id=r["saga_id"],
                transaction_id=r["transaction_id"],
                state=r["state"],
                lines=[(line["sku"], line["qty"]) for line in json.loads(r["lines"])],
                created_at=r["created_at"],
            )
            for r in rows
        ]

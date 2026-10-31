# Every statement that touches the outbox, this service's record of what it has promised to tell the rest of the system.
#
# A sale has to do two things that must either both happen or neither: mark the basket paid, and announce the sale so stock comes down. The database and the broker are separate systems and cannot be committed together, so the announcement is written here instead, inside the same transaction as the sale. One commit, nothing to lose.
# The relay in app/outbox.py reads these rows afterwards and does the actual sending.
import json
from typing import NamedTuple
from uuid import UUID

import asyncpg


# One message waiting to be sent.
class OutboxRow(NamedTuple):
    message_id: UUID
    routing_key: str
    payload: dict


class OutboxRepo:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def enqueue(
        self, conn: asyncpg.Connection, message_id: UUID, routing_key: str, payload: dict
    ) -> None:
        """Records one message as owed. Takes a connection so it joins the caller's transaction rather than committing on its own."""
        # The connection parameter is the whole point of this method. Using the pool here would open a second transaction, and the sale and its announcement could then land separately, which is exactly the failure the outbox exists to remove.
        await conn.execute(
            """
            INSERT INTO outbox (message_id, routing_key, payload)
            VALUES ($1, $2, $3::jsonb)
            """,
            message_id,
            routing_key,
            json.dumps(payload),
        )

    async def unsent(self, limit: int) -> list[OutboxRow]:
        """Reads the messages still owed, oldest first."""
        # Oldest first so stock moves in roughly the order things were sold, which keeps the lag figure meaningful instead of being an average over an arbitrary order.
        rows = await self._pool.fetch(
            """
            SELECT message_id, routing_key, payload
            FROM outbox
            WHERE sent_at IS NULL
            ORDER BY created_at
            LIMIT $1
            """,
            limit,
        )
        return [
            OutboxRow(r["message_id"], r["routing_key"], json.loads(r["payload"]))
            for r in rows
        ]

    async def mark_sent(self, message_id: UUID) -> None:
        """Records that the broker has confirmed holding this message."""
        # Only ever called after a confirmed publish. Marking first and publishing after would turn a broker failure into a sale nobody is ever told about.
        await self._pool.execute(
            "UPDATE outbox SET sent_at = now() WHERE message_id = $1", message_id
        )

    async def pending_count(self) -> int:
        """How many messages are still owed. Used by the benchmark scripts to wait for the system to settle."""
        return await self._pool.fetchval(
            "SELECT count(*) FROM outbox WHERE sent_at IS NULL"
        )

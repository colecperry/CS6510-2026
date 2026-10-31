# Proves the relay never loses a message it could not send.
#
# The relay is the only thing standing between a committed sale and stock that never moves, and the way it fails matters more than the way it succeeds: a row wrongly marked sent is a sale nobody will ever hear about again.
# Stand-in repository and publisher, because what is being tested is the order the relay does things in, not the SQL.
import asyncio

import pytest
from app.outbox import OutboxRelay

pytestmark = pytest.mark.asyncio


class FakeOutboxRepo:
    """Holds the owed messages in a list, the way the table holds them in rows."""

    def __init__(self, rows):
        self.rows = list(rows)
        self.sent = []

    async def unsent(self, limit):
        return [r for r in self.rows if r.message_id not in self.sent][:limit]

    async def mark_sent(self, message_id):
        self.sent.append(message_id)


class Row:
    def __init__(self, message_id):
        self.message_id = message_id
        self.routing_key = "sale.completed"
        self.payload = {"transactionId": f"tx-{message_id}", "lines": []}


class FakePublisher:
    """Records what was sent, and can be told the broker is refusing."""

    def __init__(self, reachable=True):
        self.sent = []
        self.reachable = reachable

    async def send_committed(self, message_id, routing_key, payload):
        if not self.reachable:
            raise ConnectionError("broker unreachable")
        self.sent.append(message_id)


async def test_a_message_the_broker_refuses_stays_unsent():
    outbox = FakeOutboxRepo([Row(1), Row(2)])
    relay = OutboxRelay(outbox, FakePublisher(reachable=False), poll_ms=10)

    with pytest.raises(ConnectionError):
        await relay._drain_once()

    assert outbox.sent == [], "a message the broker never took was marked sent"


async def test_the_same_message_goes_once_the_broker_returns():
    # The whole argument for the outbox: an outage delays stock rather than losing it.
    outbox = FakeOutboxRepo([Row(1), Row(2)])
    publisher = FakePublisher(reachable=False)
    relay = OutboxRelay(outbox, publisher, poll_ms=10)
    await relay.start()

    await asyncio.sleep(0.05)
    assert outbox.sent == []

    publisher.reachable = True
    await asyncio.sleep(2.5)          # longer than the relay's backoff after a failure
    await relay.stop()

    assert publisher.sent == [1, 2]
    assert outbox.sent == [1, 2]


async def test_a_message_is_marked_sent_only_after_the_broker_confirms():
    # Order, not outcome. Marking first would mean a crash between the two steps lost the sale silently.
    outbox = FakeOutboxRepo([Row(1)])
    publisher = FakePublisher()
    relay = OutboxRelay(outbox, publisher, poll_ms=10)

    await relay._drain_once()

    assert publisher.sent == [1] and outbox.sent == [1]


async def test_the_relay_survives_a_failure_rather_than_dying():
    # A relay that dies quietly looks exactly like a working system until someone notices stock has stopped moving.
    outbox = FakeOutboxRepo([Row(1)])
    publisher = FakePublisher(reachable=False)
    relay = OutboxRelay(outbox, publisher, poll_ms=10)
    await relay.start()

    await asyncio.sleep(0.05)
    assert not relay._task.done(), "the relay stopped at the first failure"

    await relay.stop()

# How a scan leaves the checkout service, tested without a database.
#
# Checkout cannot tell the reporting service anything, so it writes scans to the shared log instead. These tests cover the three things that writer has to get right: batch them, never make a shopper wait, and never fail a sale because reporting is behind.
import asyncio

from app.ingest import ScanWriter


class FakeScansRepo:
    """Records what was written, and how it was grouped."""

    def __init__(self):
        self.writes: list[list[str]] = []

    async def log_scans(self, skus):
        self.writes.append(list(skus))
        return sum(len(w) for w in self.writes)   # stands in for the sequence number


async def test_scans_are_written_in_batches_not_one_at_a_time():
    repo = FakeScansRepo()
    writer = ScanWriter(repo)
    await writer.start()

    for _ in range(50):
        writer.record("SKU-1")
    await writer.stop()

    # Nothing lost, and far fewer writes than scans. That gap is the point: one round trip to the database instead of fifty.
    assert sum(len(w) for w in repo.writes) == 50
    assert len(repo.writes) < 50


async def test_handing_over_a_scan_does_not_wait():
    # The shopper's request ends at record(). If this ever became something that waits, the latency gain of the whole design would quietly disappear.
    writer = ScanWriter(FakeScansRepo())
    assert not asyncio.iscoroutinefunction(writer.record)


async def test_a_full_queue_drops_scans_rather_than_failing_a_sale():
    # Losing a scan costs the ranking a little accuracy. Raising here would break a checkout because a statistics feature fell behind.
    writer = ScanWriter(FakeScansRepo())
    writer._queue._maxsize = 1

    writer.record("SKU-1")
    writer.record("SKU-1")
    writer.record("SKU-1")

    assert writer.dropped == 2


async def test_nothing_is_lost_when_the_service_stops():
    # Scans still queued at shutdown have not reached the database yet. Stopping without draining would lose them on every restart.
    repo = FakeScansRepo()
    writer = ScanWriter(repo)
    await writer.start()

    for _ in range(10):
        writer.record("SKU-1")
    await writer.stop()

    assert sum(len(w) for w in repo.writes) == 10

# How a scan leaves the checkout service, tested without a network.
#
# Analytics owns the scan log and this service cannot write to it, so scans travel as batched HTTP posts. These tests cover what that writer has to get right: batch them, never make a shopper wait, and never fail a sale because reporting is behind or down.
import asyncio

from app.ingest import ScanWriter


class FakeAnalyticsClient:
    """Records what was delivered and how it was grouped, and can be told to be unreachable."""

    def __init__(self, reachable=True):
        self.batches: list[list[str]] = []
        self._reachable = reachable

    async def send_scans(self, skus):
        if not self._reachable:
            return False          # the real client swallows the error and reports failure the same way
        self.batches.append(list(skus))
        return True


async def test_scans_are_delivered_in_batches_not_one_at_a_time():
    client = FakeAnalyticsClient()
    writer = ScanWriter(client)
    await writer.start()

    for _ in range(50):
        writer.record("SKU-1")
    await writer.stop()

    # Nothing lost, and far fewer requests than scans. That gap is the point: a handful of network calls instead of fifty.
    assert sum(len(b) for b in client.batches) == 50
    assert len(client.batches) < 50


async def test_handing_over_a_scan_does_not_wait():
    # The shopper's request ends at record(). If this ever became something that waits, the scan path would inherit the latency of another service and the whole design would quietly lose its point.
    writer = ScanWriter(FakeAnalyticsClient())
    assert not asyncio.iscoroutinefunction(writer.record)


async def test_a_full_queue_drops_scans_rather_than_failing_a_sale():
    # Losing a scan costs the ranking a little accuracy. Raising here would break a checkout because a statistics feature fell behind.
    writer = ScanWriter(FakeAnalyticsClient())
    writer._queue._maxsize = 1

    writer.record("SKU-1")
    writer.record("SKU-1")
    writer.record("SKU-1")

    assert writer.dropped == 2


async def test_nothing_is_lost_when_the_service_stops():
    # Scans still queued at shutdown have not left this process yet. Stopping without draining would lose them on every restart.
    client = FakeAnalyticsClient()
    writer = ScanWriter(client)
    await writer.start()

    for _ in range(10):
        writer.record("SKU-1")
    await writer.stop()

    assert sum(len(b) for b in client.batches) == 10


async def test_analytics_being_down_does_not_stop_the_writer():
    # The case this week added. A dead reporting service must not back the queue up, because that would eventually start dropping scans and then start costing sales.
    client = FakeAnalyticsClient(reachable=False)
    writer = ScanWriter(client)
    await writer.start()

    for _ in range(10):
        writer.record("SKU-1")
    await writer.stop()

    assert client.batches == []          # nothing got through
    assert writer.undelivered == 10      # and the loss was counted rather than hidden
    assert writer.dropped == 0           # the queue never backed up

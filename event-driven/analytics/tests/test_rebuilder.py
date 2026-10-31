# When the reporting service rebuilds its ranking, tested without a database.
#
# Nobody tells this service that a scan happened, so it asks its own database on a timer. These tests cover what it does with the two answers it can get back.
from contextlib import asynccontextmanager

from app.rebuilder import RankingRebuilder


class FakeCatalogRepo:
    def name_for(self, sku):
        return f"Item for {sku}"


class FakeScansRepo:
    def __init__(self):
        self.pruned_before: list[int] = []

    async def count_window(self, start, end, cap):
        return []

    async def prune_before(self, conn, seq):
        self.pruned_before.append(seq)


class FakeRankingRepo:
    """Answers the due-check however the test wants, and records what gets published."""

    def __init__(self, due_on: set[int]):
        self._due_on = due_on          # which poll numbers should report a rebuild is due
        self.polls = 0
        self.published: list[list] = []

    async def claim_rebuild_if_due(self):
        self.polls += 1
        return (0, 600) if self.polls in self._due_on else None

    # The rebuilder publishes and prunes inside one transaction, so the fake has to offer the same shape even though nothing here is transactional.
    @asynccontextmanager
    async def _conn(self):
        yield self

    def acquire(self):
        return self._conn()

    @asynccontextmanager
    async def transaction(self):
        yield

    async def publish(self, conn, ranked):
        self.published.append(ranked)


def build(due_on):
    scans, ranking = FakeScansRepo(), FakeRankingRepo(due_on)
    return RankingRebuilder(scans, ranking, FakeCatalogRepo(), snapshot_cap=100), scans, ranking


async def test_a_poll_that_is_not_due_does_nothing():
    rebuilder, scans, ranking = build(due_on=set())

    await rebuilder.poll_once()

    assert ranking.polls == 1
    assert ranking.published == []       # asked, told no, stopped there
    assert scans.pruned_before == []     # and nothing was thrown away


async def test_a_poll_that_is_due_publishes_a_ranking():
    rebuilder, scans, ranking = build(due_on={1})

    await rebuilder.poll_once()

    assert len(ranking.published) == 1
    # Scans below the window start can never count again, so they go in the same transaction as the publish.
    assert scans.pruned_before == [0]


async def test_most_polls_do_nothing():
    # The normal case. At roughly 1,300 scans a second a rebuild is due about every 400ms, so the great majority of polls cost one cheap query and stop.
    rebuilder, scans, ranking = build(due_on={3})

    for _ in range(10):
        await rebuilder.poll_once()

    assert ranking.polls == 10
    assert len(ranking.published) == 1

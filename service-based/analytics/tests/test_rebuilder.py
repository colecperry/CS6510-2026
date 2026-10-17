# When the reporting service rebuilds its ranking, tested without a database.
#
# Nobody tells this service that a scan happened, so it asks the database on a timer. These tests cover what it does with the two answers it can get back.
from app.rebuilder import RankingRebuilder
from common.data.catalog_repo import CatalogEntry


class FakeCatalogRepo:
    def get(self, sku):
        return CatalogEntry(sku, f"Item for {sku}", 1.0)


class FakeScansRepo:
    """Answers the due-check however the test wants, and counts rebuilds."""

    def __init__(self, due_on: set[int]):
        self._due_on = due_on          # which poll numbers should report a rebuild is due
        self.polls = 0
        self.published: list[list] = []

    async def claim_rebuild_if_due(self):
        self.polls += 1
        return (0, 600) if self.polls in self._due_on else None

    async def count_window(self, start, end, cap):
        return []

    async def publish_ranking(self, ranked, prune_before):
        self.published.append(ranked)


def build(due_on):
    repo = FakeScansRepo(due_on)
    return RankingRebuilder(repo, FakeCatalogRepo(), snapshot_cap=100), repo


async def test_a_poll_that_is_not_due_does_nothing():
    rebuilder, repo = build(due_on=set())

    await rebuilder.poll_once()

    assert repo.polls == 1
    assert repo.published == []          # asked, told no, stopped there


async def test_a_poll_that_is_due_publishes_a_ranking():
    rebuilder, repo = build(due_on={1})

    await rebuilder.poll_once()

    assert len(repo.published) == 1


async def test_most_polls_do_nothing():
    # The normal case. At roughly 1,300 scans a second a rebuild is due about every 400ms, so the great majority of polls cost one cheap query and stop.
    rebuilder, repo = build(due_on={3})

    for _ in range(10):
        await rebuilder.poll_once()

    assert repo.polls == 10
    assert len(repo.published) == 1

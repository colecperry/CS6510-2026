# Checks that the broker is actually shaped the way the services assume, rather than the way the code says it asked for.
#
# Every declaration in this system is idempotent, which means a service that asks for a queue the broker already has in a different shape is quietly given the existing one. These tests read the broker's own answer through its management API, so the shape that is really there is the one that gets asserted.
import base64
import json
import urllib.error
import urllib.request

import pytest

# The management API, which is the broker describing itself. Separate from the port the services speak on, and only the test uses it.
BASE = "http://localhost:15672/api"
AUTH = base64.b64encode(b"guest:guest").decode()

# The default virtual host, written as %2F because it is named "/" and that has to be escaped inside a URL.
VHOST = "%2F"


def api(path: str) -> dict | list:
    request = urllib.request.Request(f"{BASE}{path}")
    request.add_header("Authorization", f"Basic {AUTH}")
    with urllib.request.urlopen(request, timeout=5) as response:
        return json.loads(response.read())


@pytest.fixture(scope="module", autouse=True)
def broker_running():
    """Skips this whole file when the broker is not up, so the unit suites still run without Docker."""
    try:
        api("/overview")
    except (urllib.error.URLError, OSError) as failure:
        pytest.skip(f"broker management API unreachable: {failure}")


def test_the_exchange_is_a_durable_topic():
    """One exchange, declared identically by all three services, surviving a broker restart."""
    exchange = api(f"/exchanges/{VHOST}/selfcheckout")
    assert exchange["type"] == "topic"
    assert exchange["durable"] is True


@pytest.mark.parametrize(
    "queue,key",
    [("inventory.sales", "sale.completed"), ("analytics.scans", "items.scanned")],
)
def test_each_queue_is_durable_quorum_and_bound(queue, key):
    """A queue exists for each subscriber, keeps its contents across a restart, and is bound to the one label that service cares about."""
    described = api(f"/queues/{VHOST}/{queue}")
    assert described["durable"] is True
    assert described["type"] == "quorum", "a classic queue would lose messages the broker had accepted"

    bindings = api(f"/queues/{VHOST}/{queue}/bindings")
    keys = {b["routing_key"] for b in bindings if b["source"] == "selfcheckout"}
    assert key in keys, f"{queue} is not bound to {key}, so nothing would ever reach it"


def test_neither_queue_is_bound_to_the_others_messages():
    """The two paths stay separate: a sale must not land in the scan queue, or stock would move for a scan."""
    for queue, foreign in (("inventory.sales", "items.scanned"), ("analytics.scans", "sale.completed")):
        bindings = api(f"/queues/{VHOST}/{queue}/bindings")
        keys = {b["routing_key"] for b in bindings if b["source"] == "selfcheckout"}
        assert foreign not in keys, f"{queue} would also receive {foreign}"

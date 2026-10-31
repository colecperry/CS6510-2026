# Checks the two rules this week is built on: no service reaches another's data, and no service can reach another service at all.
#
# Every other test here asks whether the code works. These ask whether the architecture is what it claims to be, by reading the source rather than running it, so a violation is caught even on a path no test exercises.
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SERVICES = ["checkout", "inventory", "analytics"]

# Which tables belong to which service. A service naming another's table in SQL is reaching across the boundary, whatever route it took to get there.
OWNED_TABLES = {
    "checkout": {"transactions", "transaction_items", "outbox"},
    "inventory": {"stock", "applied_requests"},
    "analytics": {"scan_log", "popularity_state", "popularity_snapshot"},
}

# The catalogue is excluded on purpose: all three hold their own read-only copy, seeded identically, because it never changes while the system runs.
SHARED_BY_COPY = {"catalog"}

# The port each service listens on. Finding one of these in another service's source means that service knows where to find it, which is the thing the broker exists to remove.
PORTS = {"checkout": "8081", "inventory": "8082", "analytics": "8083"}

# The Python gateway sits under edge/ this week, beside the nginx config it is being compared against.
SOURCE_ROOT = {"gateway": "edge/gateway"}


def source_files(service: str) -> list[Path]:
    """Every Python file belonging to one service, tests excluded."""
    return sorted((ROOT / SOURCE_ROOT.get(service, service) / "app").rglob("*.py"))


def read(service: str) -> str:
    return "\n".join(f.read_text() for f in source_files(service))


@pytest.mark.parametrize("service", SERVICES)
def test_a_service_never_names_another_services_table(service):
    """The core data rule: each service's SQL mentions only the tables it owns."""
    text = read(service)
    foreign = {
        table
        for other, tables in OWNED_TABLES.items()
        if other != service
        for table in tables
    } - OWNED_TABLES[service] - SHARED_BY_COPY

    # Looks for the table name next to SQL keywords, so prose in a comment explaining the boundary does not count as crossing it.
    offenders = []
    for table in foreign:
        for keyword in ("FROM", "INTO", "UPDATE", "JOIN"):
            if f"{keyword} {table}" in text:
                offenders.append(f"{keyword} {table}")

    assert not offenders, f"{service} reaches into another service's tables: {offenders}"


@pytest.mark.parametrize("service", SERVICES)
def test_a_service_only_reads_its_own_database_settings(service):
    """A service must construct only its own settings class, so it holds no other database URL."""
    text = read(service)
    others = [s for s in SERVICES if s != service]
    offenders = [f"{o.capitalize()}Settings" for o in others if f"{o.capitalize()}Settings" in text]

    assert not offenders, f"{service} reads another service's settings: {offenders}"


@pytest.mark.parametrize("service", SERVICES)
def test_a_service_holds_no_other_services_address(service):
    """The new rule this week. A service that knows where another one lives can still call it, broker or no broker."""
    # Week 6's checkout held two service URLs and a timeout budget for each. The point of events is that those are now unrepresentable, so the check is for the addresses themselves rather than for the clients that used them.
    text = read(service)
    offenders = []
    for other, port in PORTS.items():
        if other == service:
            continue
        for needle in (f":{port}", f"{other.upper()}_URL", f"{other}_url"):
            if needle in text:
                offenders.append(needle)

    assert not offenders, f"{service} holds another service's address: {offenders}"


@pytest.mark.parametrize("service", SERVICES)
def test_a_service_never_imports_another_services_package(service):
    """Each service's code is its own. Sharing a module would make the two deploy together, whatever the runtime boundary says."""
    # All three name their package "app", so a cross-import has to be written as "inventory.app" or similar, and that is what this looks for.
    text = read(service)
    others = [s for s in SERVICES if s != service]
    offenders = [f"{o}.app" for o in others if f"{o}.app" in text]

    assert not offenders, f"{service} imports another service's package: {offenders}"


def test_no_service_makes_outbound_calls():
    """Nobody calls anybody. Last week checkout was allowed one direction of HTTP; this week there is none."""
    for service in SERVICES:
        text = read(service)
        assert "httpx" not in text, f"{service} has an HTTP client and should not need one"


def test_the_gateway_holds_no_data_access():
    """The gateway routes requests and nothing else: no database driver, no shared library, no business rules."""
    text = read("gateway")
    assert "asyncpg" not in text, "the gateway opened a database connection"
    assert "from common" not in text, "the gateway imported the shared library"


def test_the_gateway_does_not_expose_internal_endpoints():
    """The /internal paths that services used to coordinate through must not be reachable from outside."""
    # The routes themselves are gone this week, so this guards against one being reintroduced and routed by accident rather than against today's code.
    from importlib import util

    spec = util.spec_from_file_location("gw_routes", ROOT / "edge" / "gateway" / "app" / "routes.py")
    routes = util.module_from_spec(spec)
    spec.loader.exec_module(routes)

    assert routes.owner_of("/internal/stock/reduce") is None
    assert routes.owner_of("/transactions") is not None   # and the real ones still route

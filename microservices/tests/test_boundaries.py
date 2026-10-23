# Checks the one rule this week is built on: no service can reach another service's data.
#
# Every other test here asks whether the code works. These ask whether the architecture is what it claims to be, by reading the source rather than running it, so a violation is caught even on a path no test exercises.
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SERVICES = ["checkout", "inventory", "analytics"]

# Which tables belong to which service. A service naming another's table in SQL is reaching across the boundary, whatever route it took to get there.
OWNED_TABLES = {
    "checkout": {"transactions", "transaction_items", "payment_saga"},
    "inventory": {"stock", "applied_requests"},
    "analytics": {"scan_log", "popularity_state", "popularity_snapshot"},
}

# The catalogue is excluded on purpose: all three hold their own read-only copy, seeded identically, because it never changes while the system runs.
SHARED_BY_COPY = {"catalog"}


def source_files(service: str) -> list[Path]:
    """Every Python file belonging to one service, tests excluded."""
    return sorted((ROOT / service / "app").rglob("*.py"))


def read(service: str) -> str:
    return "\n".join(f.read_text() for f in source_files(service))


@pytest.mark.parametrize("service", SERVICES)
def test_a_service_never_names_another_services_table(service):
    """The core rule: each service's SQL mentions only the tables it owns."""
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


def test_only_checkout_makes_outbound_calls():
    """Inventory and analytics must not call anyone. Checkout may, because paying has to reduce stock inventory owns."""
    # Week 5 forbade all service-to-service calls. This week requires exactly one direction of them, so the check is no longer "nobody has an HTTP client" but "only the service that needs one has it".
    for service in ["inventory", "analytics"]:
        text = read(service)
        assert "httpx" not in text, f"{service} has an HTTP client and should not need one"


def test_the_gateway_holds_no_data_access():
    """The gateway routes requests and nothing else: no database driver, no shared library, no business rules."""
    text = read("gateway")
    assert "asyncpg" not in text, "the gateway opened a database connection"
    assert "from common" not in text, "the gateway imported the shared library"


def test_the_gateway_does_not_expose_internal_endpoints():
    """The /internal paths services use to coordinate must not be reachable from outside."""
    from importlib import util

    spec = util.spec_from_file_location("gw_routes", ROOT / "gateway" / "app" / "routes.py")
    routes = util.module_from_spec(spec)
    spec.loader.exec_module(routes)

    assert routes.owner_of("/internal/stock/reduce") is None
    assert routes.owner_of("/transactions") is not None   # and the real ones still route

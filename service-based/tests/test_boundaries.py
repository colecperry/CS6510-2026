# The one rule the assignment is most explicit about, checked mechanically.
#
# "Services should not communicate directly." A service that quietly grew a call to another would still pass every other test here, and the architecture claim in the submission would simply be false. So this reads the source rather than trusting it.
import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DOMAIN_SERVICES = ["checkout", "inventory", "analytics"]
# The gateway is the caller-facing tier, not a domain service. It is allowed to know where the services are; that is its whole job.
GATEWAY = "gateway"


def python_files(service: str) -> list[Path]:
    return [p for p in (ROOT / service).rglob("*.py") if ".venv" not in p.parts]


def imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text())
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


@pytest.mark.parametrize("service", DOMAIN_SERVICES)
def test_a_service_does_not_import_another_service(service):
    others = [s for s in DOMAIN_SERVICES + [GATEWAY] if s != service]
    offences = []
    for path in python_files(service):
        for module in imported_modules(path):
            root = module.split(".")[0]
            if root in others:
                offences.append(f"{path.relative_to(ROOT)} imports {module}")
    assert not offences, "a service reached into another service:\n" + "\n".join(offences)


@pytest.mark.parametrize("service", DOMAIN_SERVICES)
def test_a_service_does_not_know_another_services_address(service):
    # The addresses live in shared settings, so nothing stops a service reading one. Only this test does.
    forbidden = ["checkout_url", "inventory_url", "analytics_url", "CHECKOUT_URL",
                 "INVENTORY_URL", "ANALYTICS_URL", "localhost:808"]
    offences = []
    for path in python_files(service):
        text = path.read_text()
        for needle in forbidden:
            if needle in text:
                offences.append(f"{path.relative_to(ROOT)} mentions {needle}")
    assert not offences, "a service knows where another lives:\n" + "\n".join(offences)


@pytest.mark.parametrize("service", DOMAIN_SERVICES)
def test_a_service_does_not_carry_an_http_client(service):
    # Having no way to make an outbound request is a stronger guarantee than not currently making one.
    clients = {"httpx", "requests", "aiohttp", "urllib.request", "http.client"}
    offences = []
    for path in python_files(service):
        for module in imported_modules(path):
            if module.split(".")[0] in {c.split(".")[0] for c in clients}:
                offences.append(f"{path.relative_to(ROOT)} imports {module}")
    assert not offences, "a service can reach out over HTTP:\n" + "\n".join(offences)


def test_the_gateway_holds_no_business_logic():
    # FR-005a: the entry point forwards and nothing else. If it ever imports the shared data library, it has started doing a service's job.
    offences = []
    for path in python_files(GATEWAY):
        for module in imported_modules(path):
            if module.split(".")[0] in {"common", "asyncpg"}:
                offences.append(f"{path.relative_to(ROOT)} imports {module}")
    assert not offences, "the gateway reached into the data layer:\n" + "\n".join(offences)

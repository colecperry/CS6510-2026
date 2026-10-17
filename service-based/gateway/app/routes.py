# Decides which service owns a request, and forwards it there unchanged.
#
# This is the whole gateway. It holds no business rules, opens no database connection, and never looks inside a response. Its only knowledge is which path belongs to which service.
import os

import httpx
from fastapi import APIRouter, Request, Response

# Read straight from the environment rather than through the shared settings library, because the gateway deliberately does not depend on it. See gateway/pyproject.toml.
CHECKOUT = os.environ.get("CHECKOUT_URL", "http://localhost:8081")
INVENTORY = os.environ.get("INVENTORY_URL", "http://localhost:8082")
ANALYTICS = os.environ.get("ANALYTICS_URL", "http://localhost:8083")

# Longest matching prefix wins, so the order here does not matter.
ROUTES = {
    "/transactions": CHECKOUT,
    "/items": INVENTORY,
    "/inventory": INVENTORY,
    "/analytics": ANALYTICS,
}

router = APIRouter()

# One client for the whole process, so connections to the services are reused rather than reopened per request. Opening a connection costs more than the request itself.
client = httpx.AsyncClient(timeout=30.0)


def owner_of(path: str) -> str | None:
    """Returns the base address of the service owning this path, or None."""
    matches = [p for p in ROUTES if path == p or path.startswith(p + "/")]
    if not matches:
        return None
    return ROUTES[max(matches, key=len)]


@router.api_route(
    "/{full_path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
)
async def forward(full_path: str, request: Request) -> Response:
    """Sends the request to whichever service owns it, and returns the answer."""
    target = owner_of("/" + full_path)
    if target is None:
        # Nothing owns this path. The load client never asks for one.
        return Response(content='{"error":"NOT_FOUND","message":"No such path"}',
                        status_code=404, media_type="application/json")

    # Host must not be passed on: it still names the gateway, and the service would answer as though it were the gateway.
    headers = {k: v for k, v in request.headers.items() if k.lower() != "host"}

    upstream = await client.request(
        method=request.method,
        url=target + "/" + full_path,
        content=await request.body(),
        headers=headers,
        params=request.query_params,
    )

    # Returned as received. Hop-by-hop headers are dropped because they describe this connection, not the response, and passing them on confuses the caller.
    drop = {"content-encoding", "content-length", "transfer-encoding", "connection"}
    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        headers={k: v for k, v in upstream.headers.items() if k.lower() not in drop},
        media_type=upstream.headers.get("content-type"),
    )

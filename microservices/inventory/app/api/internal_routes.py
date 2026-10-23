# The endpoints checkout uses to move stock during a payment.
#
# Not part of the public contract and deliberately absent from the gateway's routing table, so nothing outside the system can reach them. They exist only because stock lives in this service's database and the service that sells things cannot write to it.
from pydantic import BaseModel

from app import wiring
from fastapi import APIRouter

router = APIRouter(prefix="/internal")


class StockLine(BaseModel):
    sku: str
    qty: int


class ReduceRequest(BaseModel):
    # Opaque to this service: checkout sends a saga id, but all that matters here is that the same string means the same request.
    idempotencyKey: str
    lines: list[StockLine]


class RestoreRequest(BaseModel):
    idempotencyKey: str


@router.post("/stock/reduce")
async def reduce_stock(body: ReduceRequest) -> dict:
    """Takes the given quantities off the shelf, at most once per key."""
    lines = [(line.sku, line.qty) for line in body.lines]
    already = not await wiring.stock_mover.reduce(body.idempotencyKey, lines)
    # A repeat reports success, not an error. Checkout cannot tell a lost reply from a lost request, so retrying has to be safe and has to look the same as the first time.
    return {"applied": True, "alreadyApplied": already}


@router.post("/stock/restore")
async def restore_stock(body: RestoreRequest) -> dict:
    """Puts back exactly what the named request took, when a payment could not be finished."""
    # Undoing something that was never applied, or is already undone, succeeds and changes nothing. Checkout may not know which of those it is asking for.
    restored = await wiring.stock_mover.restore(body.idempotencyKey)
    return {"restored": restored}


@router.get("/stock/applied/{idempotency_key}")
async def was_applied(idempotency_key: str) -> dict:
    """Whether this key was ever applied and not since undone."""
    # Asked by checkout at startup, when it finds a payment it began and does not know the outcome of. This service is the only one that can answer.
    return {"applied": await wiring.stock_mover.was_applied(idempotency_key)}

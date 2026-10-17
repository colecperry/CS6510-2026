# The four checkout endpoints.
#
# Each route is one line: take the request apart, call the service, return what comes back. Decisions and SQL live below this, not here.
from fastapi import APIRouter

from app import wiring
from common.schemas import (
    Receipt,
    ScanItemRequest,
    ScanResult,
    StartTransactionRequest,
    Transaction,
)

router = APIRouter()


@router.post("/transactions", status_code=201)   # 201 means "created"
async def start_transaction(body: StartTransactionRequest) -> Transaction:
    return await wiring.service.start(body.station_id)


@router.post("/transactions/{transaction_id}/items")
async def scan_item(transaction_id: str, body: ScanItemRequest) -> ScanResult:
    return await wiring.service.scan(transaction_id, body.sku)


@router.post("/transactions/{transaction_id}/complete")
async def complete_transaction(transaction_id: str) -> Receipt:
    return await wiring.service.complete(transaction_id)


# Not used by the load client. Here for debugging and for the instructor.
@router.get("/transactions/{transaction_id}")
async def get_transaction(transaction_id: str) -> Transaction:
    return await wiring.service.get(transaction_id)

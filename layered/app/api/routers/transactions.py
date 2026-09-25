# The four checkout endpoints.
#
# Each one reads the request, hands it to the transactions layer, and
# returns what comes back. No rules and no SQL live here.
from fastapi import APIRouter

from app.api import wiring
from app.schemas import Receipt, ScanItemRequest, ScanResult, StartTransactionRequest, Transaction

router = APIRouter()


@router.post("/transactions", status_code=201)
async def start_transaction(body: StartTransactionRequest) -> Transaction:
    return await wiring.transactions.start(body.station_id)


@router.post("/transactions/{transaction_id}/items")
async def scan_item(transaction_id: str, body: ScanItemRequest) -> ScanResult:
    return await wiring.transactions.scan(transaction_id, body.sku)


@router.post("/transactions/{transaction_id}/complete")
async def complete_transaction(transaction_id: str) -> Receipt:
    return await wiring.transactions.complete(transaction_id)


# Not used by the load client. Here for debugging and for the instructor.
@router.get("/transactions/{transaction_id}")
async def get_transaction(transaction_id: str) -> Transaction:
    return await wiring.transactions.get(transaction_id)

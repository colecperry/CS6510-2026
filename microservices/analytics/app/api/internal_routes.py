# Where checkout delivers the scans this service reports on.
#
# Not part of the public contract and deliberately absent from the gateway's routing table, so nothing outside the system can post scans.
# Checkout cannot write to this service's database, so a scan arrives as a request rather than an insert.
from pydantic import BaseModel

from app import wiring
from fastapi import APIRouter

router = APIRouter(prefix="/internal")


class ScanBatch(BaseModel):
    skus: list[str]


@router.post("/scans", status_code=202)
async def record_scans(batch: ScanBatch) -> dict:
    """Takes a batch of scans and records them for the ranking."""
    # 202 rather than 200: checkout does not wait for this and will not act on the answer, so accepting the batch is all that can honestly be promised.
    # Scans are counted, not keyed, so a duplicated batch would slightly overcount the ranking and nothing else. The ranking is the one thing allowed to be approximate, and keying every scan would cost more than the error it prevents.
    if batch.skus:
        await wiring.scans.record_scans(batch.skus)
    return {"accepted": len(batch.skus)}

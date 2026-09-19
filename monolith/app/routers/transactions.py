# POST /transactions, .../items, .../complete, GET /transactions/{id} - Steps 14-17.
from fastapi import APIRouter

import app.catalog_cache as catalog_cache
import app.pool as pool_module
import app.popularity as popularity
from app.errors import ConflictError, InvalidRequestError, NotFoundError
from app.models import (
    Receipt,
    ReceiptLine,
    ScanItemRequest,
    ScanResult,
    StartTransactionRequest,
    Transaction,
)

router = APIRouter()


# Starts a new transaction for a station.
@router.post("/transactions", status_code=201)
async def start_transaction(body: StartTransactionRequest) -> Transaction:
    """Starts a new, empty transaction for a station.

    Takes: body - the station ID the customer is checking out at.
    Returns: the newly created Transaction, with status OPEN.
    """
    # Check manually so a blank stationId gives our own 400, not FastAPI's 422.
    station_id = body.station_id.strip()
    if not station_id:
        raise InvalidRequestError("INVALID_REQUEST", "stationId is required")

    # Insert the new row and read back its generated fields in one step.
    row = await pool_module.pool.fetchrow(
        """
        INSERT INTO transactions (station_id)
        VALUES ($1)
        RETURNING transaction_id, station_id, status, item_count, running_total, started_at
        """,
        station_id,
    )
    return Transaction(
        transaction_id=row["transaction_id"],
        station_id=row["station_id"],
        status=row["status"],
        item_count=row["item_count"],
        running_total=float(row["running_total"]),
        started_at=row["started_at"],
    )


# Records one scanned unit into an open transaction's basket.
@router.post("/transactions/{transaction_id}/items")
async def scan_item(transaction_id: str, body: ScanItemRequest) -> ScanResult:
    """Records one scanned unit into an open transaction's basket.

    Takes: transaction_id (from the URL), body - the SKU that was scanned.
    Returns: the updated item count and running total for this transaction.
    """
    # SKU lookup is an in-memory dict read, not a database call.
    cached = catalog_cache.catalog.get(body.sku)
    if cached is None:
        raise NotFoundError("UNKNOWN_SKU", f"No such SKU: {body.sku}")
    name, unit_price = cached

    # Bumps the running counters, but only if the transaction is still OPEN.
    row = await pool_module.pool.fetchrow(
        """
        UPDATE transactions
        SET item_count = item_count + 1, running_total = running_total + $2
        WHERE transaction_id = $1 AND status = 'OPEN'
        RETURNING item_count, running_total
        """,
        transaction_id,
        unit_price,
    )
    if row is None:
        # Matched nothing above - work out whether that's a 404 or a 409.
        status_row = await pool_module.pool.fetchrow(
            "SELECT status FROM transactions WHERE transaction_id = $1", transaction_id
        )
        if status_row is None:
            raise NotFoundError("NOT_FOUND", f"No such transaction: {transaction_id}")
        raise ConflictError("TRANSACTION_NOT_OPEN", "Transaction is not open")

    # Permanent record of this scan, used later to build the receipt.
    await pool_module.pool.execute(
        """
        INSERT INTO transaction_items (transaction_id, sku, name, unit_price)
        VALUES ($1, $2, $3, $4)
        """,
        transaction_id,
        body.sku,
        name,
        unit_price,
    )

    # Feeds the popular-items hopping window - separate from the basket above.
    await popularity.record_scan(body.sku)

    return ScanResult(
        transaction_id=transaction_id,
        sku=body.sku,
        name=name,
        unit_price=unit_price,
        item_count=row["item_count"],
        running_total=float(row["running_total"]),
    )


# Closes out a transaction: finalizes it and decrements stock per SKU.
@router.post("/transactions/{transaction_id}/complete")
async def complete_transaction(transaction_id: str) -> Receipt:
    """Finalizes a transaction: marks it complete and decrements stock per SKU.

    Takes: transaction_id - which transaction to complete.
    Returns: the Receipt, itemized by SKU.
    """
    async with pool_module.pool.acquire() as conn:
        async with conn.transaction():  # all-or-nothing: status flip + every decrement
            # Only finalizes if OPEN and non-empty - both checked in one step.
            row = await conn.fetchrow(
                """
                UPDATE transactions
                SET status = 'COMPLETED', completed_at = now()
                WHERE transaction_id = $1 AND status = 'OPEN' AND item_count > 0
                RETURNING station_id, item_count, running_total, started_at, completed_at
                """,
                transaction_id,
            )
            if row is None:
                # Matched nothing above - figure out which error applies.
                status_row = await conn.fetchrow(
                    "SELECT status, item_count FROM transactions WHERE transaction_id = $1",
                    transaction_id,
                )
                if status_row is None:
                    raise NotFoundError("NOT_FOUND", f"No such transaction: {transaction_id}")
                if status_row["item_count"] == 0:
                    raise ConflictError("EMPTY_BASKET", "Cannot complete an empty transaction")
                raise ConflictError("TRANSACTION_NOT_OPEN", "Transaction already finalized")

            # Group the scanned items by SKU - one row per SKU, with quantity.
            lines = await conn.fetch(
                """
                SELECT sku, name, unit_price, count(*) AS quantity
                FROM transaction_items
                WHERE transaction_id = $1
                GROUP BY sku, name, unit_price
                ORDER BY sku
                """,
                transaction_id,
            )

            # Decrement stock per SKU, in a fixed order to avoid deadlocks.
            for line in lines:
                await conn.execute(
                    "UPDATE stock SET qty = GREATEST(qty - $2, 0) WHERE sku = $1",
                    line["sku"],
                    line["quantity"],
                )

    # Build the receipt from data already collected above.
    return Receipt(
        transaction_id=transaction_id,
        station_id=row["station_id"],
        item_count=row["item_count"],
        total_amount=float(row["running_total"]),
        started_at=row["started_at"],
        completed_at=row["completed_at"],
        lines=[
            ReceiptLine(
                sku=line["sku"],
                name=line["name"],
                unit_price=float(line["unit_price"]),
                quantity=line["quantity"],
            )
            for line in lines
        ],
    )


# Debugging/instructor use only - not exercised by the load client.
@router.get("/transactions/{transaction_id}")
async def get_transaction(transaction_id: str) -> Transaction:
    """Looks up a transaction's current status, for debugging.

    Takes: transaction_id - which transaction to look up.
    Returns: the Transaction, or a 404 if it doesn't exist.
    """
    row = await pool_module.pool.fetchrow(
        """
        SELECT transaction_id, station_id, status, item_count, running_total, started_at
        FROM transactions
        WHERE transaction_id = $1
        """,
        transaction_id,
    )
    if row is None:
        raise NotFoundError("NOT_FOUND", f"No such transaction: {transaction_id}")
    return Transaction(
        transaction_id=row["transaction_id"],
        station_id=row["station_id"],
        status=row["status"],
        item_count=row["item_count"],
        running_total=float(row["running_total"]),
        started_at=row["started_at"],
    )

# The checkout itself: start a basket, scan into it, pay, look one up.
#
# These four routes carry the money-handling rules, so most of the care in
# this file goes into never losing or double-counting a stock decrement.
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


@router.post("/transactions", status_code=201)
async def start_transaction(body: StartTransactionRequest) -> Transaction:
    """Opens a new, empty basket for a station."""
    # Checked by hand so a blank stationId returns our own 400 rather than
    # FastAPI's 422, which is the wrong error shape.
    station_id = body.station_id.strip()
    if not station_id:
        raise InvalidRequestError("INVALID_REQUEST", "stationId is required")

    # RETURNING gives back the generated id and defaults without a second
    # query to read the row we just wrote.
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


@router.post("/transactions/{transaction_id}/items")
async def scan_item(transaction_id: str, body: ScanItemRequest) -> ScanResult:
    """Adds one unit of an item to an open basket."""
    # Price lookup is a memory read, not a database call.
    cached = catalog_cache.catalog.get(body.sku)
    if cached is None:
        raise NotFoundError("UNKNOWN_SKU", f"No such SKU: {body.sku}")
    name, unit_price = cached

    # The "is it still open?" check lives inside the UPDATE rather than in a
    # separate SELECT, so another request cannot close the basket in between.
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
        # Nothing matched, which means either no such basket or it is closed.
        # Only now is it worth a second query to tell those apart.
        status_row = await pool_module.pool.fetchrow(
            "SELECT status FROM transactions WHERE transaction_id = $1", transaction_id
        )
        if status_row is None:
            raise NotFoundError("NOT_FOUND", f"No such transaction: {transaction_id}")
        raise ConflictError("TRANSACTION_NOT_OPEN", "Transaction is not open")

    # One row per scanned unit. The receipt is built from these at checkout.
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

    # Separate from the basket: this feeds the popular-items ranking.
    await popularity.record_scan(body.sku)

    return ScanResult(
        transaction_id=transaction_id,
        sku=body.sku,
        name=name,
        unit_price=unit_price,
        item_count=row["item_count"],
        running_total=float(row["running_total"]),
    )


@router.post("/transactions/{transaction_id}/complete")
async def complete_transaction(transaction_id: str) -> Receipt:
    """Pays for the basket, takes the items out of stock, returns a receipt."""
    async with pool_module.pool.acquire() as conn:
        # One transaction around everything below, so the basket is never
        # marked paid without the stock coming down, or the other way round.
        async with conn.transaction():
            # Both rules checked inside the UPDATE: still open, and not empty.
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
                # Work out which of the three reasons it was.
                status_row = await conn.fetchrow(
                    "SELECT status, item_count FROM transactions WHERE transaction_id = $1",
                    transaction_id,
                )
                if status_row is None:
                    raise NotFoundError("NOT_FOUND", f"No such transaction: {transaction_id}")
                if status_row["item_count"] == 0:
                    raise ConflictError("EMPTY_BASKET", "Cannot complete an empty transaction")
                raise ConflictError("TRANSACTION_NOT_OPEN", "Transaction already finalized")

            # Collapse the scanned units into one line per SKU, with a count.
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

            # Always in SKU order. Two checkouts holding the same two items
            # would otherwise be able to grab them in opposite orders and
            # deadlock waiting on each other.
            for line in lines:
                await conn.execute(
                    "UPDATE stock SET qty = GREATEST(qty - $2, 0) WHERE sku = $1",
                    line["sku"],
                    line["quantity"],
                )

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


# Not used by the load client. Here for debugging and for the instructor.
@router.get("/transactions/{transaction_id}")
async def get_transaction(transaction_id: str) -> Transaction:
    """Looks up one basket's current state."""
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

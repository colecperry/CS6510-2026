# The checkout rules: what may be scanned, what may be paid for, and what the shopper is told when the answer is no.
#
# Knows nothing about HTTP and writes no SQL. It raises the errors in common/errors.py and leaves the routes to turn those into responses.
# It does own one transaction boundary, in complete(): the repositories each write their own table, and paying means both writes land together or neither does.
from uuid import uuid4

import asyncpg

from app.bus.publisher import SALE_KEY
from app.data.baskets_repo import BasketsRepo, TransactionRow
from app.data.catalog_repo import CatalogRepo
from app.data.outbox_repo import OutboxRepo
from app.ingest import ScanWriter
from common.errors import ConflictError, InvalidRequestError, NotFoundError
from common.schemas import Receipt, ReceiptLine, ScanResult, Transaction


class CheckoutService:
    def __init__(
        self,
        pool: asyncpg.Pool,
        baskets: BasketsRepo,
        catalog: CatalogRepo,
        outbox: OutboxRepo,
        scans: ScanWriter,
    ):
        self._pool = pool              # held only so paying can be one transaction
        self._baskets = baskets        # baskets and receipts
        self._catalog = catalog        # prices, from memory
        self._outbox = outbox          # what this service still owes the others
        self._scans = scans            # where scans go to be counted later

    async def start(self, station_id: str) -> Transaction:
        """Opens a new empty basket for a station."""
        # Checked by hand rather than by the request model, so a blank station gets our error shape instead of the framework's.
        cleaned = station_id.strip()
        if not cleaned:
            raise InvalidRequestError("INVALID_REQUEST", "stationId is required")

        row = await self._baskets.start(cleaned)
        return _to_transaction(row)

    async def scan(self, transaction_id: str, sku: str) -> ScanResult:
        """Adds one unit of an item to an open basket."""
        entry = self._catalog.get(sku)
        if entry is None:
            raise NotFoundError("UNKNOWN_SKU", f"No such SKU: {sku}")

        counters = await self._baskets.bump_counters(transaction_id, entry.price)
        if counters is None:
            raise await self._explain_write_refusal(transaction_id)

        await self._baskets.add_line(transaction_id, sku, entry.name, entry.price)

        # Analytics owns the scan log and this service cannot write to it, so the scan is handed to something that will post it there in the background. No await: the shopper never waits for reporting.
        self._scans.record(sku)

        return ScanResult(
            transaction_id=transaction_id,
            sku=sku,
            name=entry.name,
            unit_price=entry.price,
            item_count=counters.item_count,
            running_total=counters.running_total,
        )

    async def complete(self, transaction_id: str) -> Receipt:
        """Pays for the basket and returns the receipt."""
        # One transaction, two tables: the basket becomes COMPLETED and the sale is written into the outbox together. Nothing is sent to anyone here, which is why a shopper can pay while inventory and the broker are both down.
        # Last week this spanned two services and needed a saga to repair the half-finished cases. There are none now: the two writes are one commit, and delivery is the broker's problem rather than this request's.
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                paid = await self._baskets.finalise(conn, transaction_id)
                if paid is None:
                    raise await self._explain_write_refusal(transaction_id, completing=True)

                lines = await self._baskets.receipt_lines(conn, transaction_id)

                # The id is generated here and stored with the message, so a copy the broker delivers twice still carries the id inventory already recorded and is recognised as the same sale.
                await self._outbox.enqueue(
                    conn,
                    uuid4(),
                    SALE_KEY,
                    {
                        "transactionId": transaction_id,
                        "lines": [
                            {"sku": line.sku, "qty": line.quantity} for line in lines
                        ],
                    },
                )

        return Receipt(
            transaction_id=transaction_id,
            station_id=paid.station_id,
            item_count=paid.item_count,
            total_amount=paid.running_total,
            started_at=paid.started_at,
            completed_at=paid.completed_at,
            lines=[
                ReceiptLine(
                    sku=line.sku,
                    name=line.name,
                    unit_price=line.unit_price,
                    quantity=line.quantity,
                )
                for line in lines
            ],
        )

    async def get(self, transaction_id: str) -> Transaction:
        """Looks up one basket's current state."""
        row = await self._baskets.fetch(transaction_id)
        if row is None:
            raise NotFoundError("NOT_FOUND", f"No such transaction: {transaction_id}")
        return _to_transaction(row)

    async def _explain_write_refusal(
        self, transaction_id: str, completing: bool = False
    ) -> Exception:
        """Works out why a write was refused, and builds the matching error."""
        # The database only says no, not why. This asks the follow-up question and picks the message the shopper should see. Only runs after something already failed, never on the happy path.
        state = await self._baskets.state_of(transaction_id)
        if state is None:
            return NotFoundError("NOT_FOUND", f"No such transaction: {transaction_id}")
        if completing and state.item_count == 0:
            return ConflictError("EMPTY_BASKET", "Cannot complete an empty transaction")
        if completing:
            return ConflictError("TRANSACTION_NOT_OPEN", "Transaction already finalized")
        return ConflictError("TRANSACTION_NOT_OPEN", "Transaction is not open")


# Database row to API response. The two happen to line up field for field.
def _to_transaction(row: TransactionRow) -> Transaction:
    return Transaction(
        transaction_id=row.transaction_id,
        station_id=row.station_id,
        status=row.status,
        item_count=row.item_count,
        running_total=row.running_total,
        started_at=row.started_at,
    )

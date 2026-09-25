# The checkout rules: what may be scanned, what may be paid for, and what
# the customer is told when the answer is no.
#
# Knows nothing about HTTP and writes no SQL. It raises the errors in
# app/errors.py and leaves the API layer to turn those into responses.
from app.analytics.service import AnalyticsService
from app.data.catalog_repo import CatalogRepo
from app.data.transactions_repo import TransactionsRepo
from app.errors import ConflictError, InvalidRequestError, NotFoundError
from app.schemas import (
    CatalogItem,
    CatalogResponse,
    Receipt,
    ReceiptLine,
    ScanResult,
    Transaction,
)


class TransactionService:
    def __init__(
        self,
        transactions: TransactionsRepo,
        catalog: CatalogRepo,
        analytics: AnalyticsService,
    ):
        self._transactions = transactions
        self._catalog = catalog
        self._analytics = analytics

    def list_items(self) -> CatalogResponse:
        """Returns the whole product list."""
        # Served from memory, so this makes no database call at all.
        return CatalogResponse(
            items=[
                CatalogItem(sku=e.sku, name=e.name, price=e.price)
                for e in self._catalog.all()
            ]
        )

    async def start(self, station_id: str) -> Transaction:
        """Opens a new empty basket for a station."""
        # Checked here rather than by pydantic, so a blank station gets our
        # own error shape instead of the framework's.
        cleaned = station_id.strip()
        if not cleaned:
            raise InvalidRequestError("INVALID_REQUEST", "stationId is required")

        row = await self._transactions.start(cleaned)
        return _to_transaction(row)

    async def scan(self, transaction_id: str, sku: str) -> ScanResult:
        """Adds one unit of an item to an open basket."""
        entry = self._catalog.get(sku)
        if entry is None:
            raise NotFoundError("UNKNOWN_SKU", f"No such SKU: {sku}")

        counters = await self._transactions.bump_counters(transaction_id, entry.price)
        if counters is None:
            raise await self._explain_write_refusal(transaction_id)

        await self._transactions.add_line(transaction_id, sku, entry.name, entry.price)

        # Separate from the basket: this feeds the popular-items ranking.
        await self._analytics.record_scan(sku)

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
        done = await self._transactions.complete(transaction_id)
        if done is None:
            raise await self._explain_write_refusal(transaction_id, completing=True)

        return Receipt(
            transaction_id=transaction_id,
            station_id=done.station_id,
            item_count=done.item_count,
            total_amount=done.total_amount,
            started_at=done.started_at,
            completed_at=done.completed_at,
            lines=[
                ReceiptLine(
                    sku=line.sku,
                    name=line.name,
                    unit_price=line.unit_price,
                    quantity=line.quantity,
                )
                for line in done.lines
            ],
        )

    async def get(self, transaction_id: str) -> Transaction:
        """Looks up one basket's current state."""
        row = await self._transactions.fetch(transaction_id)
        if row is None:
            raise NotFoundError("NOT_FOUND", f"No such transaction: {transaction_id}")
        return _to_transaction(row)

    async def _explain_write_refusal(
        self, transaction_id: str, completing: bool = False
    ) -> Exception:
        """Works out why a write was refused, and builds the matching error.

        Only runs after something already failed, never on the happy path.
        """
        state = await self._transactions.state_of(transaction_id)
        if state is None:
            return NotFoundError("NOT_FOUND", f"No such transaction: {transaction_id}")
        if completing and state.item_count == 0:
            return ConflictError("EMPTY_BASKET", "Cannot complete an empty transaction")
        if completing:
            return ConflictError("TRANSACTION_NOT_OPEN", "Transaction already finalized")
        return ConflictError("TRANSACTION_NOT_OPEN", "Transaction is not open")


def _to_transaction(row) -> Transaction:
    return Transaction(
        transaction_id=row.transaction_id,
        station_id=row.station_id,
        status=row.status,
        item_count=row.item_count,
        running_total=row.running_total,
        started_at=row.started_at,
    )

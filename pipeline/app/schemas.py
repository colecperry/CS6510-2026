# The shape of every request and response body the API accepts or returns.
#
# Defined once here and shared by all four layers, so the wire format lives
# in one place. Mirrors spec/self-checkout-openapi.yaml.
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel


# Everything below inherits this, which renames fields on the way out:
# running_total becomes runningTotal, as the contract requires.
class ApiModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class CatalogItem(ApiModel):
    sku: str
    name: str
    price: float


class CatalogResponse(ApiModel):
    items: list[CatalogItem]


class StartTransactionRequest(ApiModel):
    # Optional here even though the contract requires it. If pydantic
    # rejected a blank value we would return FastAPI's 422, which is the
    # wrong error shape, so the transactions layer checks it instead.
    station_id: str = ""


class Transaction(ApiModel):
    transaction_id: str
    station_id: str
    status: Literal["OPEN", "COMPLETED", "CANCELLED"]
    item_count: int
    running_total: float
    started_at: datetime


class ScanItemRequest(ApiModel):
    sku: str


class ScanResult(ApiModel):
    transaction_id: str
    sku: str
    name: str
    unit_price: float
    item_count: int
    running_total: float


class ReceiptLine(ApiModel):
    sku: str
    name: str
    unit_price: float
    quantity: int


class Receipt(ApiModel):
    transaction_id: str
    station_id: str
    item_count: int
    total_amount: float
    started_at: datetime
    completed_at: datetime
    lines: list[ReceiptLine]


class LowStockAlert(ApiModel):
    sku: str
    name: str
    current_stock: int
    threshold: int
    triggered_at: datetime


class LowStockResponse(ApiModel):
    threshold: int
    generated_at: datetime
    alerts: list[LowStockAlert]


class PopularItem(ApiModel):
    sku: str
    name: str
    scan_count: int
    rank: int


class PopularItemsResponse(ApiModel):
    window_size: int
    slide_interval: int
    window_start: int
    window_end: int
    computed_at: datetime
    items: list[PopularItem]


class ApiError(ApiModel):
    error: str
    message: str

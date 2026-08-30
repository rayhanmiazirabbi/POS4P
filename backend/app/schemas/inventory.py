from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, NonNegativeInt, field_validator, model_validator

from app.schemas.base import ApiModel

Quantity = Annotated[Decimal, Field(gt=0)]
UnitCost = Annotated[Decimal, Field(ge=0)]
BatchNumber = Annotated[str, Field(min_length=1, max_length=100)]
Reason = Annotated[str, Field(min_length=3, max_length=280)]


class ReceiveBatchRequest(ApiModel):
    """Opening stock or a goods receipt against a single store product."""

    store_product_id: UUID
    batch_number: BatchNumber
    expiry_date: date | None = None
    unit_cost: UnitCost
    quantity: Quantity
    reference_type: Annotated[str | None, Field(max_length=80)] = None
    reference_id: UUID | None = None


class AdjustmentRequest(ApiModel):
    """Signed stock correction: positive adds units, negative removes them."""

    store_product_id: UUID
    batch_id: UUID | None = None
    quantity: Annotated[Decimal, Field()]
    reason: Reason
    damage: bool = False

    @field_validator("quantity")
    @classmethod
    def _non_zero(cls, value: Decimal) -> Decimal:
        if value == 0:
            raise ValueError("quantity must be a non-zero signed amount")
        return value


class BalanceResponse(ApiModel):
    store_product_id: UUID
    on_hand: Decimal
    reserved: Decimal
    available: Decimal


class BatchAvailableResponse(ApiModel):
    batch_id: UUID
    batch_number: str
    expiry_date: date | None
    received_at: datetime
    unit_cost: Decimal
    available: Decimal
    expired: bool


class StockResponse(BalanceResponse):
    low_stock: bool


class ExpiringBatchResponse(BatchAvailableResponse):
    store_product_id: UUID
    days_until_expiry: int


class LowStockResponse(ApiModel):
    store_product_id: UUID
    sku: str
    on_hand: Decimal
    minimum_stock: Decimal


class AllocationLine(ApiModel):
    batch_id: UUID
    quantity: Decimal


class AllocationResultResponse(ApiModel):
    """Explicit partial/failure contract: ``ok`` is false when stock was short."""

    ok: bool
    requested: Decimal
    allocated: Decimal
    shortfall: Decimal
    allocations: list[AllocationLine]


class BatchResponse(ApiModel):
    id: UUID
    batch_number: str
    expiry_date: date | None
    unit_cost: Decimal
    received_at: datetime


class MovementResponse(ApiModel):
    id: UUID
    store_product_id: UUID
    batch_id: UUID | None
    movement_type: str
    quantity: Decimal
    occurred_at: datetime


class ReceiveBatchResponse(ApiModel):
    batch: MovementBatchPayload
    movement: MovementResponse
    balance: BalanceResponse


class IntakeCustomProduct(ApiModel):
    name: Annotated[str, Field(min_length=1, max_length=240)]
    unit: Annotated[str, Field(min_length=1, max_length=40)]
    barcode: Annotated[str | None, Field(min_length=1, max_length=64)] = None


class IntakeShelf(ApiModel):
    sale_price: Annotated[Decimal | None, Field(ge=0, decimal_places=2)] = None
    sku: Annotated[str | None, Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$")] = None
    barcode: Annotated[str | None, Field(min_length=1, max_length=64)] = None
    rack: Annotated[str | None, Field(min_length=1, max_length=80)] = None
    minimum_stock: Annotated[Decimal | None, Field(ge=0)] = None


class InventoryIntakeRequest(ApiModel):
    source: Literal["opening_stock", "supplier_receive"]
    store_product_id: UUID | None = None
    pharmacy_product_id: UUID | None = None
    catalog_product_id: UUID | None = None
    custom_product: IntakeCustomProduct | None = None
    shelf: IntakeShelf = Field(default_factory=IntakeShelf)
    quantity: Quantity
    unit_cost: UnitCost | None = None
    batch_number: BatchNumber | None = None
    expiry_date: date | None = None
    supplier_id: UUID | None = None
    reference: Annotated[str | None, Field(max_length=160)] = None

    @model_validator(mode="after")
    def validate_intake(self) -> InventoryIntakeRequest:
        identities = (
            self.store_product_id,
            self.pharmacy_product_id,
            self.catalog_product_id,
            self.custom_product,
        )
        if sum(value is not None for value in identities) != 1:
            raise ValueError("Provide exactly one product identity")
        if self.source == "supplier_receive" and self.unit_cost is None:
            raise ValueError("Supplier receipts require unitCost")
        if self.store_product_id is None and self.shelf.sale_price is None:
            raise ValueError("New shelf items require salePrice")
        return self


class InventoryIntakeResponse(ApiModel):
    store_product_id: UUID
    pharmacy_product_id: UUID
    name: str
    sku: str
    barcode: str | None
    sale_price: Decimal
    rack: str | None
    unit: str
    adopted: bool
    batch: BatchResponse
    movement: MovementResponse
    balance: BalanceResponse


class MovementBatchPayload(ApiModel):
    id: UUID
    batch_number: str
    expiry_date: date | None
    unit_cost: Decimal
    received_at: datetime


class RebuildResultResponse(ApiModel):
    store_id: UUID
    rebuilt: NonNegativeInt


class TransferLineRequest(ApiModel):
    store_product_id: UUID
    quantity: Decimal


class TransferCreateRequest(ApiModel):
    transfer_number: Annotated[str, Field(min_length=1, max_length=60)]
    from_store_id: UUID
    to_store_id: UUID
    items: Annotated[list[TransferLineRequest], Field(min_length=1)]


class TransferResponse(ApiModel):
    id: UUID
    transfer_number: str
    from_store_id: UUID
    to_store_id: UUID
    status: str
    shipped_at: datetime | None
    received_at: datetime | None


class TransferItemResponse(TransferLineRequest):
    id: UUID


# --- movement ledger ------------------------------------------------------------


class MovementLedgerResponse(ApiModel):
    """One ledger row dressed for the shelf: who, what batch, why."""

    id: UUID
    store_product_id: UUID
    sku: str
    product_name: str
    batch_id: UUID | None
    batch_number: str | None
    movement_type: str
    quantity: Decimal
    reason: str | None
    reference_type: str | None
    occurred_at: datetime


# --- racks ----------------------------------------------------------------------


class RackResponse(ApiModel):
    """One physical rack and how many active shelf items sit on it."""

    rack: str
    item_count: NonNegativeInt


class RackRenameRequest(ApiModel):
    """Merge or retitle a rack: every item on ``from`` moves to ``to``."""

    store_id: UUID
    from_rack: Annotated[str, Field(min_length=1, max_length=80)]
    to_rack: Annotated[str, Field(min_length=1, max_length=80)]


# --- stocktakes -----------------------------------------------------------------


class StocktakeCreateRequest(ApiModel):
    note: Annotated[str | None, Field(max_length=280)] = None


class StocktakeLineRequest(ApiModel):
    """A counted quantity for one product; resubmitting a line replaces it."""

    store_product_id: UUID
    counted_quantity: Annotated[Decimal, Field(ge=0)]


class StocktakeLineResponse(ApiModel):
    store_product_id: UUID
    sku: str
    product_name: str
    counted_quantity: Decimal
    system_quantity: Decimal
    variance: Decimal


class StocktakeResponse(ApiModel):
    id: UUID
    store_id: UUID
    status: str
    note: str | None
    created_at: datetime
    completed_at: datetime | None
    lines: list[StocktakeLineResponse]


class StocktakeSummaryResponse(ApiModel):
    stocktake: StocktakeResponse
    corrected_lines: NonNegativeInt
    unchanged_lines: NonNegativeInt


# --- reorder suggestions ----------------------------------------------------------


class ReorderSuggestionResponse(ApiModel):
    """Below-minimum product with a suggested order quantity attached."""

    store_product_id: UUID
    sku: str
    product_name: str
    available: Decimal
    minimum_stock: Decimal
    suggested_quantity: Decimal

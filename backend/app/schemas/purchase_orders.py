from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated
from uuid import UUID

from pydantic import Field

from app.domains.purchase_orders import PurchaseOrderStatus
from app.schemas.base import ApiModel

Quantity = Annotated[Decimal, Field(gt=0, decimal_places=4)]
Money = Annotated[Decimal, Field(ge=0, decimal_places=2)]
Note = Annotated[str | None, Field(max_length=2000)]


class PurchaseOrderItemCreate(ApiModel):
    """A line to restock. ``name`` is free text; catalogue/pharmacy links are
    optional hints used later when converting into a purchase."""

    name: Annotated[str, Field(min_length=1, max_length=240)]
    quantity: Quantity
    est_unit_cost: Money | None = None
    catalog_product_id: UUID | None = None
    pharmacy_product_id: UUID | None = None

    @property
    def clean_name(self) -> str:
        return self.name.strip()


class PurchaseOrderItemUpdate(ApiModel):
    name: Annotated[str | None, Field(min_length=1, max_length=240)] = None
    quantity: Quantity | None = None
    est_unit_cost: Money | None = None


class PurchaseOrderCreateRequest(ApiModel):
    supplier_id: UUID | None = None
    expected_at: date | None = None
    note: Note = None
    items: Annotated[list[PurchaseOrderItemCreate], Field(max_length=200)] = Field(
        default_factory=list
    )


class PurchaseOrderConvertRequest(ApiModel):
    """Supplier fallback for orders placed without one; purchases require it."""

    supplier_id: UUID | None = None


class PurchaseOrderItemResponse(ApiModel):
    id: UUID
    purchase_order_id: UUID
    catalog_product_id: UUID | None
    pharmacy_product_id: UUID | None
    name: str
    quantity: Decimal
    est_unit_cost: Decimal | None
    received_quantity: Decimal = Decimal(0)
    remaining_quantity: Decimal = Decimal(0)


class PurchaseOrderResponse(ApiModel):
    id: UUID
    organization_id: UUID
    store_id: UUID
    supplier_id: UUID | None
    supplier_name: str | None = None
    status: PurchaseOrderStatus
    expected_at: date | None
    note: str | None
    ordered_at: datetime | None
    closed_at: datetime | None
    cancelled_at: datetime | None
    created_at: datetime
    item_count: int = 0
    ordered_quantity: Decimal = Decimal(0)
    received_quantity: Decimal = Decimal(0)
    items: list[PurchaseOrderItemResponse] = Field(default_factory=list)


class SkippedLine(ApiModel):
    item_id: UUID
    name: str
    reason: str


class PurchaseOrderConvertResult(ApiModel):
    purchase_id: UUID
    purchase_order_id: UUID
    converted_count: int
    skipped: list[SkippedLine] = Field(default_factory=list)

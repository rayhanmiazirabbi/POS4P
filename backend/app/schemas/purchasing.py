from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated
from uuid import UUID

from pydantic import Field

from app.domains.purchasing import PurchaseStatus
from app.schemas.base import ApiModel

Quantity = Annotated[Decimal, Field(gt=0, decimal_places=4)]
Money = Annotated[Decimal, Field(ge=0, decimal_places=2)]


class PurchaseItemCreate(ApiModel):
    store_product_id: UUID
    quantity: Quantity
    unit_cost: Money
    batch_number: Annotated[str, Field(min_length=1, max_length=100)]
    expiry_date: date | None = None


class PurchaseCreateRequest(ApiModel):
    supplier_id: UUID
    invoice_number: Annotated[str | None, Field(max_length=100)] = None
    note: Annotated[str | None, Field(max_length=2000)] = None
    purchased_at: date | None = None
    items: Annotated[list[PurchaseItemCreate], Field(min_length=1)]


class PurchaseItemResponse(ApiModel):
    id: UUID
    purchase_id: UUID
    store_product_id: UUID
    quantity: Decimal
    batch_number: str
    expiry_date: date | None
    unit_cost: Decimal | None = None


class PurchaseResponse(ApiModel):
    id: UUID
    organization_id: UUID
    store_id: UUID
    supplier_id: UUID
    status: PurchaseStatus
    invoice_number: str | None
    note: str | None
    purchased_at: date
    confirmed_at: datetime | None
    total_amount: Decimal | None = None
    items: list[PurchaseItemResponse] = []


class PurchaseReturnLine(ApiModel):
    purchase_item_id: UUID
    quantity: Quantity


class PurchaseReturnRequest(ApiModel):
    lines: Annotated[list[PurchaseReturnLine], Field(min_length=1)]
    note: Annotated[str | None, Field(max_length=2000)] = None

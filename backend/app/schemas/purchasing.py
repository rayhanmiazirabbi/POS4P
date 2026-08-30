from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated
from uuid import UUID

from pydantic import Field, model_validator

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
    line_total: Decimal | None = None


class PurchaseResponse(ApiModel):
    id: UUID
    organization_id: UUID
    store_id: UUID
    supplier_id: UUID
    status: PurchaseStatus
    invoice_number: str | None
    receipt_number: str | None
    note: str | None
    purchased_at: date
    confirmed_at: datetime | None
    total_amount: Decimal | None = None
    items: list[PurchaseItemResponse] = Field(default_factory=list)


class ReceiveCustomProduct(ApiModel):
    name: Annotated[str, Field(min_length=1, max_length=240)]
    unit: Annotated[str, Field(min_length=1, max_length=40)]
    barcode: Annotated[str | None, Field(min_length=1, max_length=64)] = None


class ReceiveShelf(ApiModel):
    sale_price: Money | None = None
    sku: Annotated[str | None, Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$")] = None
    barcode: Annotated[str | None, Field(min_length=1, max_length=64)] = None
    rack: Annotated[str | None, Field(min_length=1, max_length=80)] = None
    minimum_stock: Annotated[Decimal | None, Field(ge=0)] = None


class PurchaseReceiveItem(ApiModel):
    store_product_id: UUID | None = None
    pharmacy_product_id: UUID | None = None
    catalog_product_id: UUID | None = None
    custom_product: ReceiveCustomProduct | None = None
    shelf: ReceiveShelf = Field(default_factory=ReceiveShelf)
    quantity: Quantity
    unit_cost: Money | None = None
    line_total: Money | None = None
    batch_number: Annotated[str | None, Field(min_length=1, max_length=100)] = None
    expiry_date: date | None = None

    @model_validator(mode="after")
    def validate_identity_and_cost(self) -> PurchaseReceiveItem:
        identities = (self.store_product_id, self.pharmacy_product_id, self.catalog_product_id, self.custom_product)
        if sum(value is not None for value in identities) != 1:
            raise ValueError("Provide exactly one product identity")
        if self.unit_cost is not None and self.line_total is not None:
            raise ValueError("Provide at most one of unitCost or lineTotal")
        return self


class PurchaseReceivePayment(ApiModel):
    method: Annotated[str, Field(min_length=2, max_length=40, pattern=r"^[a-z][a-z0-9_-]*$")]
    amount: Annotated[Decimal, Field(gt=0, decimal_places=2)]
    provider_reference: Annotated[str | None, Field(min_length=1, max_length=160)] = None


class PurchaseReceiveRequest(ApiModel):
    supplier_id: UUID
    invoice_number: Annotated[str | None, Field(max_length=100)] = None
    note: Annotated[str | None, Field(max_length=2000)] = None
    purchased_at: date | None = None
    total_amount: Money | None = None
    items: Annotated[list[PurchaseReceiveItem], Field(min_length=1)]
    payments: list[PurchaseReceivePayment] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_total_covers_entered_costs(self) -> PurchaseReceiveRequest:
        if self.total_amount is None:
            return self
        entered_total = sum(
            (
                Decimal(item.line_total)
                if item.line_total is not None
                else Decimal(item.quantity) * Decimal(item.unit_cost)
                if item.unit_cost is not None
                else Decimal(0)
            )
            for item in self.items
        )
        if Decimal(self.total_amount) < entered_total:
            raise ValueError("totalAmount cannot be less than entered item costs")
        return self


class PurchaseReceiptLine(ApiModel):
    purchase_item_id: UUID
    store_product_id: UUID
    name: str
    sku: str
    unit: str
    quantity: Decimal
    unit_cost: Decimal
    line_total: Decimal
    batch_number: str
    expiry_date: date | None


class PurchaseReceiptPayment(ApiModel):
    method: str
    amount: Decimal
    provider_reference: str | None = None


class PurchaseReceiptResponse(ApiModel):
    purchase_id: UUID
    receipt_number: str
    supplier_id: UUID
    supplier_name: str
    invoice_number: str | None
    purchased_at: date
    confirmed_at: datetime
    total_amount: Decimal
    paid_amount: Decimal
    credit_amount: Decimal
    supplier_balance_after: Decimal
    lines: list[PurchaseReceiptLine]
    payments: list[PurchaseReceiptPayment]


class PurchaseReturnLine(ApiModel):
    purchase_item_id: UUID
    quantity: Quantity


class PurchaseReturnRequest(ApiModel):
    lines: Annotated[list[PurchaseReturnLine], Field(min_length=1)]
    note: Annotated[str | None, Field(max_length=2000)] = None

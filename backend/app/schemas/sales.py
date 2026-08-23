from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Annotated
from uuid import UUID

from pydantic import Field

from app.domains.payments import PaymentMethod
from app.domains.sales import SaleChannel, SaleStatus
from app.schemas.base import ApiModel
from app.schemas.payments import Money, PaymentResponse

Quantity = Annotated[Decimal, Field(gt=0, decimal_places=4)]


class SaleLineCreate(ApiModel):
    store_product_id: UUID
    quantity: Quantity


class PaymentInput(ApiModel):
    method: PaymentMethod
    amount: Money
    received_amount: Money | None = None
    provider_reference: Annotated[str | None, Field(max_length=160)] = None


class SaleCreateRequest(ApiModel):
    customer_id: UUID | None = None
    discount: Money = Decimal("0.00")
    items: Annotated[list[SaleLineCreate], Field(min_length=1)]
    payments: Annotated[list[PaymentInput], Field(min_length=1)]
    # Clients echo their locally computed display totals; the server always
    # recomputes from ``store_products.sale_price`` and ignores these values.
    subtotal: Money | None = None
    total: Money | None = None


class SaleItemResponse(ApiModel):
    id: UUID
    store_product_id: UUID
    product_name: str
    quantity: Decimal
    unit_price: Decimal
    line_total: Decimal


class SaleResponse(ApiModel):
    id: UUID
    organization_id: UUID
    store_id: UUID
    customer_id: UUID | None
    channel: SaleChannel
    status: SaleStatus
    subtotal: Decimal
    discount: Decimal
    total: Decimal
    receipt_number: str | None
    void_reason: str | None = None
    created_at: datetime
    items: list[SaleItemResponse] = []
    payments: list[PaymentResponse] = []


class SaleReturnLine(ApiModel):
    sale_item_id: UUID
    quantity: Quantity


class SaleReturnRequest(ApiModel):
    reason: Annotated[str, Field(min_length=1, max_length=240)]
    lines: Annotated[list[SaleReturnLine], Field(min_length=1)]


class SaleReturnResponse(ApiModel):
    id: UUID
    sale_id: UUID
    reason: str
    total: Decimal
    created_at: datetime


class SaleVoidRequest(ApiModel):
    reason: Annotated[str, Field(min_length=1, max_length=2000)]


# Resolve deferred annotations eagerly so route registration never sees a
# partially-built model regardless of module import order.
for _model in (SaleLineCreate, PaymentInput, SaleCreateRequest, SaleItemResponse, SaleResponse, SaleReturnLine, SaleReturnRequest, SaleReturnResponse, SaleVoidRequest):
    _model.model_rebuild()

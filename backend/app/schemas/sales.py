from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, model_validator

from app.domains.payments import PaymentMethod
from app.domains.sales import SaleChannel, SaleStatus
from app.schemas.base import ApiModel
from app.schemas.payments import Money, PaymentResponse

Quantity = Annotated[Decimal, Field(gt=0, decimal_places=4)]


class SaleLineCreate(ApiModel):
    store_product_id: UUID
    quantity: Quantity
    discount: DiscountInput | None = None


class DiscountInput(ApiModel):
    mode: Literal["percentage", "flat"]
    value: Money


class SaleChargeInput(ApiModel):
    kind: Literal["delivery", "other"]
    amount: Money
    label: Annotated[str | None, Field(min_length=1, max_length=120)] = None

    @model_validator(mode="after")
    def require_other_label(self) -> SaleChargeInput:
        if self.kind == "other" and self.amount > 0 and not self.label:
            raise ValueError("Other fee requires a label")
        return self


class AdvanceApplicationInput(ApiModel):
    amount: Money
    reference: Annotated[str | None, Field(max_length=160)] = None


class PaymentInput(ApiModel):
    method: PaymentMethod
    amount: Money
    received_amount: Money | None = None
    provider_reference: Annotated[str | None, Field(max_length=160)] = None


class SaleCreateRequest(ApiModel):
    customer_id: UUID | None = None
    discount: Money = Decimal("0.00")
    global_discount: DiscountInput | None = None
    charges: list[SaleChargeInput] = Field(default_factory=list)
    advance_application: AdvanceApplicationInput | None = None
    discount_approval_token: Annotated[str | None, Field(max_length=200)] = None
    items: Annotated[list[SaleLineCreate], Field(min_length=1)]
    payments: Annotated[list[PaymentInput], Field(min_length=1)]
    # Clients echo their locally computed display totals; the server always
    # recomputes from ``store_products.sale_price`` and ignores these values.
    subtotal: Money | None = None
    total: Money | None = None

    @model_validator(mode="after")
    def structured_discount_excludes_legacy(self) -> SaleCreateRequest:
        if self.global_discount is not None and self.discount != 0:
            raise ValueError("Use either discount or globalDiscount, not both")
        return self


class SaleItemResponse(ApiModel):
    id: UUID
    store_product_id: UUID
    product_name: str
    quantity: Decimal
    unit_price: Decimal
    discount_mode: str | None = None
    discount_value: Decimal = Decimal("0.00")
    discount_amount: Decimal = Decimal("0.00")
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
    line_discount: Decimal = Decimal("0.00")
    global_discount: Decimal = Decimal("0.00")
    delivery_charge: Decimal = Decimal("0.00")
    other_fee_label: str | None = None
    other_fee: Decimal = Decimal("0.00")
    advance_applied: Decimal = Decimal("0.00")
    advance_reference: str | None = None
    amount_due_now: Decimal = Decimal("0.00")
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
    advance_restored: Decimal = Decimal("0.00")
    created_at: datetime


class SaleVoidRequest(ApiModel):
    reason: Annotated[str, Field(min_length=1, max_length=2000)]


class DiscountApprovalRequest(ApiModel):
    phone: Annotated[str, Field(min_length=6, max_length=32)]
    pin: Annotated[str, Field(min_length=4, max_length=6, pattern=r"^[0-9]+$")]
    items: Annotated[list[SaleLineCreate], Field(min_length=1)]
    discount: Money = Decimal("0.00")
    global_discount: DiscountInput | None = None
    charges: list[SaleChargeInput] = Field(default_factory=list)


class DiscountApprovalResponse(ApiModel):
    token: str
    expires_at: datetime
    approved_by: str


# Resolve deferred annotations eagerly so route registration never sees a
# partially-built model regardless of module import order.
for _model in (DiscountInput, SaleLineCreate, SaleChargeInput, AdvanceApplicationInput, PaymentInput, SaleCreateRequest, SaleItemResponse, SaleResponse, SaleReturnLine, SaleReturnRequest, SaleReturnResponse, SaleVoidRequest, DiscountApprovalRequest, DiscountApprovalResponse):
    _model.model_rebuild()

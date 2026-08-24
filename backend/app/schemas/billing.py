from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import Field

from app.schemas.base import ApiModel


class BillingPlanResponse(ApiModel):
    id: UUID
    code: str
    name: str
    monthly_amount: Decimal
    entitlements: dict
    active: bool


class SubscriptionResponse(ApiModel):
    id: UUID
    plan_id: UUID
    plan_code: str | None = None
    plan_name: str | None = None
    status: str
    effective_status: str
    current_period_end: datetime | None = None
    grace_period_end: datetime | None = None
    entitlements: dict = Field(default_factory=dict)


class PlanChangeRequest(ApiModel):
    plan_code: str


class BillingInvoiceResponse(ApiModel):
    id: UUID
    invoice_number: str
    amount: Decimal
    issued_at: datetime
    paid_at: datetime | None = None
    status: str


class ProviderEventRequest(ApiModel):
    """Wire shape of a billing provider webhook delivery.

    ``extra="forbid"`` also guards against silently accepting a payload from a
    provider version we have never integrated.
    """

    event_id: str = Field(min_length=8, max_length=160)
    type: str
    organization_id: UUID | None = None
    data: dict = Field(default_factory=dict)

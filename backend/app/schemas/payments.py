from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Annotated
from uuid import UUID

from pydantic import Field

from app.domains.payments import PaymentMethod, PaymentStatus
from app.schemas.base import ApiModel

Money = Annotated[Decimal, Field(ge=0, decimal_places=2)]


class PaymentResponse(ApiModel):
    id: UUID
    organization_id: UUID
    store_id: UUID
    reference_type: str
    reference_id: UUID
    customer_id: UUID | None = None
    method: PaymentMethod
    amount: Decimal
    received_amount: Decimal | None = None
    status: PaymentStatus
    provider_reference: str | None = None
    created_at: datetime


class PaymentStatusUpdateRequest(ApiModel):
    status: PaymentStatus
    provider_reference: Annotated[str | None, Field(max_length=160)] = None

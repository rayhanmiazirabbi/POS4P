from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Annotated
from uuid import UUID

from pydantic import Field, field_validator

from app.schemas.base import ApiModel


def _canonical_phone(value: str | None) -> str | None:
    """Reject anything that is not a Bangladesh mobile, storing the canonical form.

    Shared by create and update so a phone can never enter the table by one route
    in a shape the other would refuse -- the column is uniquely indexed on this
    value, and two spellings of one number would defeat duplicate detection.
    """
    from app.services.customers import normalize_phone

    if value is None:
        return None
    normalized = normalize_phone(value)
    if normalized is None:
        raise ValueError("Phone number is not a valid Bangladesh mobile number")
    return normalized


class CustomerCreate(ApiModel):
    name: Annotated[str, Field(min_length=1, max_length=160)]
    normalized_phone: Annotated[str | None, Field(max_length=32)] = None
    email: Annotated[str | None, Field(max_length=254)] = None
    preferences: dict = {}

    _check_phone = field_validator("normalized_phone")(_canonical_phone)


class CustomerUpdate(ApiModel):
    name: Annotated[str | None, Field(min_length=1, max_length=160)] = None
    #: Correcting a mistyped phone is the commonest edit a shop makes; the value is
    #: canonicalised the same way as on create so the two cannot drift apart.
    normalized_phone: Annotated[str | None, Field(max_length=32)] = None
    email: Annotated[str | None, Field(max_length=254)] = None
    preferences: dict | None = None

    _check_phone = field_validator("normalized_phone")(_canonical_phone)


class CustomerResponse(ApiModel):
    id: UUID
    organization_id: UUID
    name: str
    normalized_phone: str | None
    email: str | None
    due_balance: Decimal
    advance_balance: Decimal = Decimal("0.00")
    preferences: dict
    active: bool
    created_at: datetime


class CustomerAddressCreate(ApiModel):
    label: Annotated[str, Field(min_length=1, max_length=40)]
    address_line: Annotated[str, Field(min_length=1, max_length=300)]
    city: Annotated[str | None, Field(max_length=100)] = None
    postal_code: Annotated[str | None, Field(max_length=20)] = None


class CustomerAddressResponse(ApiModel):
    id: UUID
    customer_id: UUID
    label: str
    address_line: str
    city: str | None
    postal_code: str | None
    active: bool
    created_at: datetime


class CustomerHistorySummary(ApiModel):
    """Netted purchase history for one customer.

    ``total_spent`` is ``None`` when the caller's role may not see lifetime spend;
    ``total_due`` stays populated because a cashier needs it to take a payment.
    """

    customer_id: UUID
    sale_count: int
    total_spent: Decimal | None = None
    total_refunded: Decimal
    total_due: Decimal


class CustomerPurchaseRow(ApiModel):
    sale_id: UUID
    store_id: UUID
    receipt_number: str | None
    total: Decimal
    status: str
    created_at: datetime

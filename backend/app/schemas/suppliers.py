from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Annotated
from uuid import UUID

from pydantic import Field

from app.domains.suppliers import SupplierStatus
from app.schemas.base import ApiModel

SupplierName = Annotated[str, Field(min_length=2, max_length=180)]
MoneyAmount = Annotated[Decimal, Field(decimal_places=2)]


class SupplierCreateRequest(ApiModel):
    name: SupplierName
    phone: Annotated[str | None, Field(max_length=32)] = None
    address: Annotated[str | None, Field(max_length=2000)] = None


class SupplierUpdateRequest(ApiModel):
    name: SupplierName | None = None
    phone: Annotated[str | None, Field(max_length=32)] = None
    address: Annotated[str | None, Field(max_length=2000)] = None


class SupplierStatusUpdateRequest(ApiModel):
    status: SupplierStatus


class SupplierResponse(ApiModel):
    id: UUID
    organization_id: UUID
    name: str
    phone: str | None
    address: str | None
    status: SupplierStatus
    created_at: datetime


class SupplierProductCreateRequest(ApiModel):
    pharmacy_product_id: UUID
    supplier_sku: Annotated[str | None, Field(max_length=80)] = None
    preferred: bool = False


class SupplierProductResponse(ApiModel):
    id: UUID
    supplier_id: UUID
    pharmacy_product_id: UUID
    supplier_sku: str | None
    preferred: bool


class LedgerEntryCreateRequest(ApiModel):
    """Payment or adjustment body; the amount is a signed Decimal string."""

    amount: MoneyAmount
    note: Annotated[str | None, Field(max_length=500)] = None
    reference_type: Annotated[str | None, Field(max_length=80)] = None
    reference_id: UUID | None = None


class LedgerEntryResponse(ApiModel):
    id: UUID
    supplier_id: UUID
    store_id: UUID
    entry_type: str
    amount: Decimal
    reference_type: str | None
    reference_id: UUID | None
    note: str | None
    payment_method: str | None
    provider_reference: str | None
    created_at: datetime


class SupplierBalanceResponse(ApiModel):
    supplier_id: UUID
    balance: Decimal

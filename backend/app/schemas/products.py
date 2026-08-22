from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Annotated
from uuid import UUID

from pydantic import Field, field_validator

from app.schemas.base import ApiModel

ProductName = Annotated[str, Field(min_length=1, max_length=240)]
Barcode = Annotated[str, Field(min_length=1, max_length=64)]
Sku = Annotated[str, Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$")]
Unit = Annotated[str, Field(min_length=1, max_length=40)]
Rack = Annotated[str, Field(min_length=1, max_length=80)]
Money = Annotated[Decimal, Field(ge=0, decimal_places=2)]
Quantity = Annotated[Decimal, Field(ge=0)]


def _strip(value: str | None) -> str | None:
    return None if value is None else value.strip()


class PharmacyProductCreateRequest(ApiModel):
    """``organizationId`` comes from the token, never the body."""

    name: ProductName
    unit: Unit
    catalog_product_id: UUID | None = None
    barcode: Barcode | None = None

    @field_validator("name", "barcode")
    @classmethod
    def _strip_fields(cls, value: str | None) -> str | None:
        return _strip(value)


class PharmacyProductUpdateRequest(ApiModel):
    name: ProductName | None = None
    unit: Unit | None = None
    barcode: Barcode | None = None

    @field_validator("name", "barcode")
    @classmethod
    def _strip_fields(cls, value: str | None) -> str | None:
        return _strip(value)


class PharmacyProductStatusRequest(ApiModel):
    active: bool


class PharmacyProductResponse(ApiModel):
    id: UUID
    organization_id: UUID
    catalog_product_id: UUID | None
    name: str
    barcode: str | None
    unit: str
    active: bool
    created_at: datetime


class StoreProductEnableRequest(ApiModel):
    pharmacy_product_id: UUID
    sku: Sku
    sale_price: Money
    minimum_stock: Quantity = Decimal("0")
    rack: Rack | None = None

    @field_validator("rack")
    @classmethod
    def _strip_rack(cls, value: str | None) -> str | None:
        return _strip(value)


class StoreProductUpdateRequest(ApiModel):
    sku: Sku | None = None
    sale_price: Money | None = None
    minimum_stock: Quantity | None = None
    rack: Rack | None = None

    @field_validator("rack")
    @classmethod
    def _strip_rack(cls, value: str | None) -> str | None:
        return _strip(value)


class StoreProductStatusRequest(ApiModel):
    active: bool


class StoreProductResponse(ApiModel):
    id: UUID
    organization_id: UUID
    store_id: UUID
    pharmacy_product_id: UUID
    sku: str
    sale_price: Decimal
    minimum_stock: Decimal
    rack: str | None
    active: bool
    created_at: datetime


class StoreProductPriceResponse(ApiModel):
    id: UUID
    store_product_id: UUID
    price: Decimal
    effective_at: datetime

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal
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


class ShelfItemResponse(StoreProductResponse):
    """A shelf row with the two fields a counter cannot sell without.

    ``name`` and ``barcode`` live on ``PharmacyProduct``, one join away, and the
    shelf endpoint did not make it -- so every counter listed bare SKUs
    (``PARA-500``, not ``Paracetamol 500mg``) and a scanned barcode had nothing on
    the device to match against. The second half is the one that mattered: the whole
    point of caching the shelf is selling through an outage, and a scanner that has
    to ask the server which product it just read cannot do that.

    A superset of ``StoreProductResponse`` rather than a replacement, so the
    management screens reading the same endpoint are unaffected.
    """

    name: str
    unit: str
    barcode: str | None
    generic_name: str | None = None
    strength: str | None = None
    dosage_form_id: UUID | None = None
    dosage_form: str | None = None
    manufacturer_id: UUID | None = None
    manufacturer: str | None = None
    available_quantity: Decimal = Decimal(0)


class StoreProductPriceResponse(ApiModel):
    id: UUID
    store_product_id: UUID
    price: Decimal
    effective_at: datetime


class CatalogSearchItemResponse(ApiModel):
    """One merged row of ``GET /products/search``.

    ``kind`` says which identity the row carries: ``catalog`` rows are global
    catalogue entries (possibly linked to an org product), ``custom`` rows exist
    only in the org. The shelf fields (``store_product_id``, ``sku``,
    ``sale_price``, ``available_quantity``) are populated only for the store the
    token is pinned to.
    """

    kind: Literal["catalog", "custom"]
    catalog_product_id: UUID | None = None
    pharmacy_product_id: UUID | None = None
    store_product_id: UUID | None = None
    shop_status: Literal["on_shelf", "in_org", "absent"] = "absent"
    name: str
    barcode: str | None = None
    generic_name: str | None = None
    strength: str | None = None
    dosage_form_id: UUID | None = None
    dosage_form: str | None = None
    manufacturer_id: UUID | None = None
    manufacturer: str | None = None
    package_size: Decimal | None = None
    package_unit: str | None = None
    prescription_required: bool = False
    reference_unit_price: Decimal | None = None
    reference_strip_price: Decimal | None = None
    sale_price: Decimal | None = None
    available_quantity: Decimal | None = None
    sku: str | None = None
    matched_field: Literal["barcode", "sku", "name", "genericName", "alias", "strength", "dosageForm"]
    match_quality: Literal["exact", "partial", "fuzzy", "supporting"]
    matched_text: str
    match_score: float = Field(ge=0, le=1)


class CatalogAlternativeItemResponse(ApiModel):
    """One row of ``GET /products/alternatives``: another brand of the same generic.

    The search row minus everything a query earned (``kind``, ``barcode``, the
    ``matched_*`` metadata) plus what an alternative is *relative to* -- the
    asked-about row's strength and dosage form, so a screen can lead with the
    like-for-like swap and label the rest. Only catalogue rows appear: an org
    product with no catalogue link has no generic name to match on, so it is
    invisible to this comparison by construction rather than by filter.
    """

    catalog_product_id: UUID
    pharmacy_product_id: UUID | None = None
    store_product_id: UUID | None = None
    shop_status: Literal["on_shelf", "in_org", "absent"] = "absent"
    name: str
    generic_name: str | None = None
    strength: str | None = None
    dosage_form_id: UUID | None = None
    dosage_form: str | None = None
    manufacturer_id: UUID | None = None
    manufacturer: str | None = None
    package_size: Decimal | None = None
    package_unit: str | None = None
    prescription_required: bool = False
    reference_unit_price: Decimal | None = None
    reference_strip_price: Decimal | None = None
    sale_price: Decimal | None = None
    available_quantity: Decimal | None = None
    sku: str | None = None
    same_strength: bool = False
    same_dosage_form: bool = False


class ProductAdoptRequest(ApiModel):
    """Adopt a catalogue entry onto this shop and its shelf."""

    catalog_product_id: UUID
    store_id: UUID | None = None
    sku: Sku | None = None
    sale_price: Money | None = None
    minimum_stock: Quantity = Decimal("0")
    rack: Rack | None = None

    @field_validator("rack", "sku")
    @classmethod
    def _strip_fields(cls, value: str | None) -> str | None:
        return _strip(value)


class ProductAdoptResponse(ApiModel):
    pharmacy_product: PharmacyProductResponse
    store_product: StoreProductResponse

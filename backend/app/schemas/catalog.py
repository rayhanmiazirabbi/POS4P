from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Annotated
from uuid import UUID

from pydantic import Field

from app.schemas.base import ApiModel

Name = Annotated[str, Field(min_length=1, max_length=160)]
CountryCode = Annotated[str, Field(min_length=2, max_length=2)]
PackageName = Annotated[str, Field(min_length=1, max_length=40)]


def _clean(value: str) -> str:
    return value.strip()


class ReferenceCreateRequest(ApiModel):
    name: Name
    country_code: CountryCode | None = None
    active: bool = True

    @property
    def clean_name(self) -> str:
        return _clean(self.name)


class ReferenceUpdateRequest(ApiModel):
    name: Name | None = None
    country_code: CountryCode | None = None
    active: bool | None = None


class ReferenceResponse(ApiModel):
    id: UUID
    name: str
    country_code: str | None = None
    active: bool
    created_at: datetime


class IngredientCreateRequest(ApiModel):
    name: Name
    active: bool = True

    @property
    def clean_name(self) -> str:
        return _clean(self.name)


class DosageFormCreateRequest(ApiModel):
    name: Name
    active: bool = True

    @property
    def clean_name(self) -> str:
        return _clean(self.name)


class ProductIngredientIn(ApiModel):
    """An ingredient line of a product's combination."""

    active_ingredient_id: UUID
    strength: Decimal | None = None
    unit: Annotated[str | None, Field(max_length=30)] = None


class BarcodeIn(ApiModel):
    barcode: Annotated[str, Field(min_length=4, max_length=64)]

    @property
    def clean(self) -> str:
        return _clean(self.barcode)


class AliasIn(ApiModel):
    alias: Annotated[str, Field(min_length=1, max_length=240)]

    @property
    def clean(self) -> str:
        return _clean(self.alias)


class ProductCreateRequest(ApiModel):
    name: Annotated[str, Field(min_length=1, max_length=240)]
    manufacturer_id: UUID | None = None
    dosage_form_id: UUID | None = None
    strength: Annotated[str | None, Field(max_length=100)] = None
    package_size: Decimal = Decimal(1)
    package_unit: PackageName
    prescription_required: bool = False
    country_code: CountryCode
    active: bool = True
    ingredients: list[ProductIngredientIn] = Field(default_factory=list)
    barcodes: list[BarcodeIn] = Field(default_factory=list)
    aliases: list[AliasIn] = Field(default_factory=list)

    @property
    def clean_name(self) -> str:
        return _clean(self.name)


class ProductUpdateRequest(ApiModel):
    name: Annotated[str | None, Field(min_length=1, max_length=240)] = None
    manufacturer_id: UUID | None = None
    dosage_form_id: UUID | None = None
    strength: Annotated[str | None, Field(max_length=100)] = None
    package_size: Decimal | None = None
    package_unit: PackageName | None = None
    prescription_required: bool | None = None
    country_code: CountryCode | None = None
    active: bool | None = None


class ProductIngredientResponse(ApiModel):
    active_ingredient_id: UUID
    strength: Decimal | None = None
    unit: str | None = None


class CatalogProductResponse(ApiModel):
    id: UUID
    name: str
    manufacturer_id: UUID | None = None
    dosage_form_id: UUID | None = None
    strength: str | None = None
    package_size: Decimal
    package_unit: str
    prescription_required: bool
    country_code: str
    active: bool
    ingredients: list[ProductIngredientResponse] = Field(default_factory=list)
    barcodes: list[str] = Field(default_factory=list)
    aliases: list[str] = Field(default_factory=list)
    created_at: datetime


class CatalogRevisionResponse(ApiModel):
    id: UUID
    catalog_product_id: UUID
    revision: int
    data: dict
    changed_by_user_id: UUID | None = None
    created_at: datetime

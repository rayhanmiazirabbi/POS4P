from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Annotated
from uuid import UUID

from pydantic import Field

from app.schemas.base import ApiModel

Money = Annotated[Decimal, Field(ge=0, decimal_places=2)]


class StorefrontUpsertRequest(ApiModel):
    slug: Annotated[str, Field(min_length=1, max_length=100, pattern=r"^[a-z0-9-]+$")]
    display_name: Annotated[str, Field(min_length=1, max_length=160)]
    enabled: bool = False
    custom_domain: Annotated[str | None, Field(max_length=255)] = None


class StorefrontResponse(ApiModel):
    id: UUID
    organization_id: UUID
    store_id: UUID
    slug: str
    display_name: str
    enabled: bool
    custom_domain: str | None = None
    created_at: datetime


class ListingUpsertRequest(ApiModel):
    online_name: Annotated[str | None, Field(max_length=240)] = None
    description: Annotated[str | None, Field(max_length=4000)] = None
    online_price: Money | None = None
    listed: bool = False
    pickup_enabled: bool = True
    delivery_enabled: bool = False


class ListingResponse(ApiModel):
    id: UUID
    store_id: UUID
    store_product_id: UUID
    online_name: str | None = None
    description: str | None = None
    online_price: Decimal | None = None
    listed: bool
    pickup_enabled: bool
    delivery_enabled: bool


class PublicCatalogueItem(ApiModel):
    store_product_id: UUID
    name: str
    price: Decimal
    pickup_enabled: bool
    delivery_enabled: bool
    prescription_required: bool = False

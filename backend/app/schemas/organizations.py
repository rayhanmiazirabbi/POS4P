from __future__ import annotations

import re
from datetime import datetime
from typing import Annotated
from uuid import UUID

from pydantic import Field, field_validator

from app.models import RecordStatus, Role
from app.schemas.base import ApiModel
from app.schemas.stores import (
    CurrencyCode,
    StoreResponse,
    Timezone,
    normalize_currency,
    normalize_timezone,
)

SLUG_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")

OrganizationName = Annotated[str, Field(min_length=2, max_length=160)]
OrganizationSlug = Annotated[str, Field(min_length=2, max_length=160)]


def slugify(value: str) -> str:
    """Derive a URL-safe slug so callers may omit it on create."""
    collapsed = re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-")
    return collapsed[:160].strip("-")


def normalize_slug(value: str) -> str:
    candidate = value.strip().lower()
    if not SLUG_PATTERN.match(candidate):
        raise ValueError("Slug must be lowercase alphanumeric words separated by single hyphens")
    return candidate


class OrganizationSettings(ApiModel):
    """Tenant-wide defaults.

    Each key has a default so an organization created today keeps working when a
    later release adds a setting, and so stores can inherit sane values on create.
    """

    default_timezone: Timezone = "Asia/Dhaka"
    default_currency: CurrencyCode = "BDT"
    locale: Annotated[str, Field(min_length=2, max_length=10)] = "en-BD"
    require_pin_for_discounts: bool = True
    expiry_alert_days: Annotated[int, Field(ge=1, le=365)] = 90
    low_stock_threshold_days: Annotated[int, Field(ge=1, le=180)] = 14
    allow_negative_stock: bool = False
    receipt_footer: str | None = None

    @field_validator("default_timezone")
    @classmethod
    def _known_timezone(cls, value: str) -> str:
        return normalize_timezone(value)

    @field_validator("default_currency")
    @classmethod
    def _supported_currency(cls, value: str) -> str:
        return normalize_currency(value)


class OrganizationSettingsUpdate(ApiModel):
    """Partial patch: only keys present in the body are written."""

    default_timezone: Timezone | None = None
    default_currency: CurrencyCode | None = None
    locale: Annotated[str | None, Field(min_length=2, max_length=10)] = None
    require_pin_for_discounts: bool | None = None
    expiry_alert_days: Annotated[int | None, Field(ge=1, le=365)] = None
    low_stock_threshold_days: Annotated[int | None, Field(ge=1, le=180)] = None
    allow_negative_stock: bool | None = None
    receipt_footer: str | None = None

    @field_validator("default_timezone")
    @classmethod
    def _known_timezone(cls, value: str | None) -> str | None:
        return None if value is None else normalize_timezone(value)

    @field_validator("default_currency")
    @classmethod
    def _supported_currency(cls, value: str | None) -> str | None:
        return None if value is None else normalize_currency(value)


class OrganizationCreateRequest(ApiModel):
    """The caller becomes the owner; no user or tenant id is accepted from the body."""

    name: OrganizationName
    slug: OrganizationSlug | None = None
    settings: OrganizationSettingsUpdate | None = None

    @field_validator("slug")
    @classmethod
    def _valid_slug(cls, value: str | None) -> str | None:
        return None if value is None else normalize_slug(value)


class OrganizationUpdateRequest(ApiModel):
    name: OrganizationName | None = None
    slug: OrganizationSlug | None = None

    @field_validator("slug")
    @classmethod
    def _valid_slug(cls, value: str | None) -> str | None:
        return None if value is None else normalize_slug(value)


class OrganizationResponse(ApiModel):
    """Field-for-field mirror of the ``Organization`` type in ``@pharmacy/types``."""

    id: UUID
    name: str
    slug: str
    status: RecordStatus
    created_at: datetime


class OrganizationProfileResponse(OrganizationResponse):
    settings: OrganizationSettings


class OrganizationSettingsResponse(ApiModel):
    organization_id: UUID
    settings: OrganizationSettings


class OrganizationCreateResponse(ApiModel):
    """Create returns the bootstrapped membership too, so the client can skip a round trip."""

    organization: OrganizationProfileResponse
    role: Role
    user_id: UUID


class CurrentOrganizationResponse(ApiModel):
    """The validated tenant context behind the presented access token."""

    organization: OrganizationResponse
    role: Role
    user_id: UUID
    store_id: UUID | None = None
    store: StoreResponse | None = None
    settings: OrganizationSettings

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, field_validator

from app.models import RecordStatus
from app.schemas.base import ApiModel

#: ``Currency`` in ``@pharmacy/types`` is the single literal ``'BDT'``; accepting
#: anything else would hand clients a value their type system cannot represent.
SUPPORTED_CURRENCIES = frozenset({"BDT"})

StoreName = Annotated[str, Field(min_length=2, max_length=160)]
StoreCode = Annotated[
    str, Field(min_length=2, max_length=40, pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
]
Timezone = Annotated[str, Field(min_length=3, max_length=64)]
CurrencyCode = Annotated[str, Field(min_length=3, max_length=3)]
CutoffHour = Annotated[int, Field(ge=0, le=23)]


def normalize_timezone(value: str) -> str:
    """Reject unknown IANA keys at the edge: business-day cuts depend on this."""
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError(f"Unknown timezone '{value}'") from exc
    return value


def normalize_currency(value: str) -> str:
    normalized = value.strip().upper()
    if normalized not in SUPPORTED_CURRENCIES:
        raise ValueError(f"Unsupported currency '{value}'")
    return normalized


class StoreSettings(ApiModel):
    """Branch operating preferences.

    Every field carries a default so a freshly created store is usable at once and
    downstream modules never have to branch on a missing key.
    """

    receipt_header: str | None = None
    receipt_footer: str | None = None
    business_day_cutoff_hour: CutoffHour = 0
    low_stock_alerts: bool = True
    allow_offline_sales: bool = True
    print_receipt_by_default: bool = True


class StoreSettingsUpdate(ApiModel):
    """Partial patch: only keys present in the body are written."""

    receipt_header: str | None = None
    receipt_footer: str | None = None
    business_day_cutoff_hour: CutoffHour | None = None
    low_stock_alerts: bool | None = None
    allow_offline_sales: bool | None = None
    print_receipt_by_default: bool | None = None


class StoreCreateRequest(ApiModel):
    """No ``organizationId`` field: the tenant comes from the request context only."""

    name: StoreName
    code: StoreCode = "MAIN"
    timezone: Timezone | None = None
    currency: CurrencyCode | None = None
    settings: StoreSettingsUpdate | None = None

    @field_validator("code")
    @classmethod
    def _upper_code(cls, value: str) -> str:
        return value.strip().upper()

    @field_validator("timezone")
    @classmethod
    def _known_timezone(cls, value: str | None) -> str | None:
        return None if value is None else normalize_timezone(value)

    @field_validator("currency")
    @classmethod
    def _supported_currency(cls, value: str | None) -> str | None:
        return None if value is None else normalize_currency(value)


class StoreUpdateRequest(ApiModel):
    name: StoreName | None = None
    timezone: Timezone | None = None
    currency: CurrencyCode | None = None

    @field_validator("timezone")
    @classmethod
    def _known_timezone(cls, value: str | None) -> str | None:
        return None if value is None else normalize_timezone(value)

    @field_validator("currency")
    @classmethod
    def _supported_currency(cls, value: str | None) -> str | None:
        return None if value is None else normalize_currency(value)


class StoreStatusUpdateRequest(ApiModel):
    status: RecordStatus
    reason: Annotated[str | None, Field(max_length=280)] = None


class StoreResponse(ApiModel):
    """Field-for-field mirror of the ``Store`` type in ``@pharmacy/types``."""

    id: UUID
    organization_id: UUID
    name: str
    code: str
    timezone: str
    currency: str
    status: RecordStatus
    created_at: datetime


class StoreProfileResponse(StoreResponse):
    settings: StoreSettings


class StoreSettingsResponse(ApiModel):
    store_id: UUID
    settings: StoreSettings


class StoreOperatingStatusResponse(ApiModel):
    """Operating status plus the store-local clock that reports are cut against."""

    store_id: UUID
    status: RecordStatus
    operational: bool
    timezone: str
    local_time: datetime
    business_date: date

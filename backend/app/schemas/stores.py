from __future__ import annotations

import base64
import binascii
from datetime import date, datetime
from typing import Annotated
from urllib.parse import urlparse
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
ReceiptCopy = Annotated[str | None, Field(max_length=1000)]
ReceiptContact = Annotated[str | None, Field(max_length=320)]
ReceiptPaperWidth = Annotated[int, Field(ge=48, le=210)]

RECEIPT_LOGO_MAX_BYTES = 200_000
RECEIPT_LOGO_MIME_TYPES = frozenset({"image/png", "image/jpeg", "image/webp"})


def validate_receipt_logo(value: str | None) -> str | None:
    """Accept a remote HTTPS image or a deliberately small raster data URL."""
    if value is None:
        return None
    if value.startswith("data:"):
        if len(value) > 270_000:
            raise ValueError("Receipt logo must be 200 KB or smaller")
        try:
            header, encoded = value.split(",", 1)
            mime, encoding = header[5:].split(";", 1)
            if mime not in RECEIPT_LOGO_MIME_TYPES or encoding != "base64":
                raise ValueError
            decoded = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise ValueError("Receipt logo must be a PNG, JPEG, or WebP data URL") from exc
        if len(decoded) > RECEIPT_LOGO_MAX_BYTES:
            raise ValueError("Receipt logo must be 200 KB or smaller")
        signatures = {
            "image/png": decoded.startswith(b"\x89PNG\r\n\x1a\n"),
            "image/jpeg": decoded.startswith(b"\xff\xd8\xff"),
            "image/webp": decoded.startswith(b"RIFF") and decoded[8:12] == b"WEBP",
        }
        if not signatures[mime]:
            raise ValueError("Receipt logo content does not match its image type")
        return value
    parsed = urlparse(value)
    if parsed.scheme != "https" or not parsed.netloc or len(value) > 2048:
        raise ValueError("Receipt logo URL must be an HTTPS URL up to 2048 characters")
    return value


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

    receipt_header: ReceiptCopy = None
    receipt_footer: ReceiptCopy = None
    receipt_logo: str | None = None
    receipt_business_name: ReceiptContact = None
    receipt_address: ReceiptCopy = None
    receipt_phone: ReceiptContact = None
    receipt_email: ReceiptContact = None
    receipt_tax_id: ReceiptContact = None
    receipt_paper_width_mm: ReceiptPaperWidth = 80
    receipt_show_logo: bool = True
    receipt_show_business_name: bool = True
    receipt_show_store_name: bool = True
    receipt_show_contact_details: bool = True
    receipt_show_header: bool = True
    receipt_show_receipt_number: bool = True
    receipt_show_date_time: bool = True
    receipt_show_customer: bool = True
    receipt_show_cashier: bool = True
    receipt_show_items: bool = True
    receipt_show_item_quantity: bool = True
    receipt_show_unit_price: bool = True
    receipt_show_line_total: bool = True
    receipt_show_subtotal: bool = True
    receipt_show_discounts: bool = True
    receipt_show_charges: bool = True
    receipt_show_total: bool = True
    receipt_show_payments: bool = True
    receipt_show_cash_received: bool = True
    receipt_show_change_due: bool = True
    receipt_show_footer: bool = True
    business_day_cutoff_hour: CutoffHour = 0
    low_stock_alerts: bool = True
    allow_offline_sales: bool = True
    print_receipt_by_default: bool = True

    _valid_receipt_logo = field_validator("receipt_logo")(validate_receipt_logo)


class StoreSettingsUpdate(ApiModel):
    """Partial patch: only keys present in the body are written."""

    receipt_header: ReceiptCopy = None
    receipt_footer: ReceiptCopy = None
    receipt_logo: str | None = None
    receipt_business_name: ReceiptContact = None
    receipt_address: ReceiptCopy = None
    receipt_phone: ReceiptContact = None
    receipt_email: ReceiptContact = None
    receipt_tax_id: ReceiptContact = None
    receipt_paper_width_mm: ReceiptPaperWidth | None = None
    receipt_show_logo: bool | None = None
    receipt_show_business_name: bool | None = None
    receipt_show_store_name: bool | None = None
    receipt_show_contact_details: bool | None = None
    receipt_show_header: bool | None = None
    receipt_show_receipt_number: bool | None = None
    receipt_show_date_time: bool | None = None
    receipt_show_customer: bool | None = None
    receipt_show_cashier: bool | None = None
    receipt_show_items: bool | None = None
    receipt_show_item_quantity: bool | None = None
    receipt_show_unit_price: bool | None = None
    receipt_show_line_total: bool | None = None
    receipt_show_subtotal: bool | None = None
    receipt_show_discounts: bool | None = None
    receipt_show_charges: bool | None = None
    receipt_show_total: bool | None = None
    receipt_show_payments: bool | None = None
    receipt_show_cash_received: bool | None = None
    receipt_show_change_due: bool | None = None
    receipt_show_footer: bool | None = None
    business_day_cutoff_hour: CutoffHour | None = None
    low_stock_alerts: bool | None = None
    allow_offline_sales: bool | None = None
    print_receipt_by_default: bool | None = None

    _valid_receipt_logo = field_validator("receipt_logo")(validate_receipt_logo)


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

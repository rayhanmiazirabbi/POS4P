from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from pydantic import Field

from app.schemas.base import ApiModel


class CandidateMatch(ApiModel):
    pharmacy_product_id: UUID
    name: str
    score: Decimal


class InvoiceLineResult(ApiModel):
    description: str
    quantity: Decimal
    unit_cost: Decimal
    confidence: Decimal
    candidates: list[CandidateMatch] = Field(default_factory=list)


class VoiceCartItem(ApiModel):
    store_product_id: UUID
    name: str
    sku: str | None = None
    quantity: Decimal
    confidence: Decimal


class ReorderSuggestion(ApiModel):
    store_product_id: UUID
    name: str
    sku: str | None = None
    available: Decimal
    minimum_stock: Decimal
    suggested_order_quantity: Decimal


class ExpirySuggestion(ApiModel):
    store_product_id: UUID
    batch_number: str
    expiry_date: datetime | None = None
    available: Decimal


class AnomalyFinding(ApiModel):
    kind: str
    day: str | None = None
    detail: dict = Field(default_factory=dict)


class AIJobResponse(ApiModel):
    id: UUID
    job_type: str
    status: str
    provider: str | None = None
    model_version: str | None = None
    confidence: Decimal | None = None
    result: dict[str, Any] | None = None
    error: str | None = None
    created_at: datetime
    completed_at: datetime | None = None


class AIJobCreateRequest(ApiModel):
    job_type: str
    input: dict = Field(default_factory=dict)


class AIConfirmationRequest(ApiModel):
    decision: str = Field(pattern="^(accepted|rejected)$")
    notes: str | None = Field(default=None, max_length=2000)


class AIConfirmationResponse(ApiModel):
    id: UUID
    job_id: UUID
    confirmed_by_user_id: UUID
    decision: str
    notes: str | None = None
    created_at: datetime


class PurchaseDraftRequest(ApiModel):
    """Per-line candidate selection for turning a confirmed OCR job into a draft.

    The human picks the catalogue match for each line they accept; nothing is
    ordered automatically, ever.
    """

    supplier_id: UUID
    selections: list[LineSelection]

    class LineSelection(ApiModel):
        line_index: int = Field(ge=0)
        pharmacy_product_id: UUID

from __future__ import annotations

from datetime import datetime
from typing import Annotated
from uuid import UUID

from pydantic import Field

from app.domains.prescriptions import PrescriptionStatus
from app.schemas.base import ApiModel


class PrescriptionCreateRequest(ApiModel):
    customer_id: UUID | None = None
    order_id: UUID | None = None
    prescriber_name: Annotated[str | None, Field(max_length=160)] = None
    prescription_number: Annotated[str | None, Field(max_length=100)] = None
    expires_at: datetime | None = None


class PrescriptionAttachRequest(ApiModel):
    order_id: UUID


class PrescriptionFileRequest(ApiModel):
    object_key: Annotated[str, Field(min_length=1, max_length=500)]
    content_type: Annotated[str, Field(min_length=1, max_length=100)]
    checksum: Annotated[str, Field(min_length=1, max_length=128)]


class PrescriptionReviewRequest(ApiModel):
    status: PrescriptionStatus
    notes: Annotated[str | None, Field(max_length=4000)] = None


class PrescriptionResponse(ApiModel):
    id: UUID
    organization_id: UUID
    customer_id: UUID | None = None
    order_id: UUID | None = None
    status: PrescriptionStatus
    prescriber_name: str | None = None
    prescription_number: str | None = None
    expires_at: datetime | None = None
    created_at: datetime
    files: list[PrescriptionFileResponse] = []


class PrescriptionFileResponse(ApiModel):
    id: UUID
    object_key: str
    content_type: str
    checksum: str
    uploaded_at: datetime


class PrescriptionReviewResponse(ApiModel):
    id: UUID
    prescription_id: UUID
    status: PrescriptionStatus
    pharmacist_user_id: UUID
    notes: str | None = None
    reviewed_at: datetime

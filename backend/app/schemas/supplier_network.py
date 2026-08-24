from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import Field

from app.schemas.base import ApiModel


class InviteCreateRequest(ApiModel):
    supplier_name: str = Field(min_length=1, max_length=180)
    contact_phone: str | None = Field(default=None, max_length=32)
    contact_email: str | None = Field(default=None, max_length=255)
    note: str | None = Field(default=None, max_length=2000)
    expires_in_days: int = Field(default=14, ge=1, le=90)


class InviteResponse(ApiModel):
    id: UUID
    supplier_name: str
    contact_phone: str | None = None
    contact_email: str | None = None
    note: str | None = None
    status: str
    expires_at: datetime
    decided_at: datetime | None = None
    accepted_supplier_id: UUID | None = None
    created_at: datetime


class InviteCreatedResponse(InviteResponse):
    """The plaintext token appears exactly once, at creation time."""

    invite_token: str


class InviteAcceptRequest(ApiModel):
    token: str = Field(min_length=16, max_length=256)


class AcknowledgementCreateRequest(ApiModel):
    note: str | None = Field(default=None, max_length=2000)


class AcknowledgementDecisionRequest(ApiModel):
    token: str = Field(min_length=16, max_length=256)
    decision: str = Field(pattern="^(acknowledged|declined)$")
    response_note: str | None = Field(default=None, max_length=2000)


class AcknowledgementResponse(ApiModel):
    id: UUID
    purchase_id: UUID
    supplier_id: UUID
    status: str
    note: str | None = None
    response_note: str | None = None
    requested_by_user_id: UUID
    decided_at: datetime | None = None
    created_at: datetime

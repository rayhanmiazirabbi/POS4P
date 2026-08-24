from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import Field

from app.schemas.base import ApiModel


class AuditLogResponse(ApiModel):
    id: UUID
    action: str
    entity_type: str
    entity_id: UUID | None = None
    actor_user_id: UUID | None = None
    device_id: UUID | None = None
    store_id: UUID | None = None
    request_id: str
    before_data: dict[str, Any] | None = None
    after_data: dict[str, Any] | None = None
    created_at: datetime


class AuditSearchRequest(ApiModel):
    """Body form of the same filters, used by the export endpoint.

    A CSV download cannot carry a long query string comfortably, so the owner UI
    posts the current filter set instead.
    """

    action: str | None = Field(default=None, max_length=120)
    entity_type: str | None = Field(default=None, max_length=120)
    entity_id: UUID | None = None
    actor_user_id: UUID | None = None
    date_from: datetime | None = None
    date_to: datetime | None = None
    q: str | None = Field(default=None, max_length=200)

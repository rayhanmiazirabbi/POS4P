from __future__ import annotations

from datetime import datetime
from typing import Annotated
from uuid import UUID

from pydantic import Field

from app.domains.sync import DeviceStatus
from app.schemas.base import ApiModel


class DeviceCreate(ApiModel):
    name: Annotated[str, Field(min_length=1, max_length=120)]
    device_key: Annotated[str, Field(min_length=1, max_length=160)]


class DeviceResponse(ApiModel):
    id: UUID
    store_id: UUID
    name: str
    device_key: str
    status: DeviceStatus
    created_at: datetime


class DeviceRevokeRequest(ApiModel):
    reason: Annotated[str | None, Field(max_length=500)] = None


class SyncEventEnvelopeIn(ApiModel):
    """One offline mutation, as a device uploads it.

    Cross-cutting rule 5 puts ``device_id``/``organization_id``/``store_id``/``user_id``
    on the envelope, and request models forbid unknown keys -- so before these were
    declared, an envelope that followed the rule was rejected outright. They are
    accepted here but never *trusted*: the values are checked against the bearer
    token's own claims at ingest and a mismatch is refused, so the envelope cannot
    be used to write into another tenant, store, or device's stream.

    ``created_at`` is the device's clock and is likewise untrusted -- it is kept as
    evidence, while the server stamps its own ``received_at``.
    """

    event_id: UUID
    event_type: Annotated[str, Field(min_length=1, max_length=120)]
    client_sequence: Annotated[int, Field(ge=0)]
    payload: dict = Field(default_factory=dict)
    created_at: datetime | None = None
    device_id: UUID | None = None
    organization_id: UUID | None = None
    store_id: UUID | None = None
    user_id: UUID | None = None


class IngestRequest(ApiModel):
    events: Annotated[list[SyncEventEnvelopeIn], Field(min_length=1)]


class SyncAck(ApiModel):
    event_id: UUID
    server_sequence: int | None = None
    duplicate: bool = False
    error_code: str | None = None


class IngestResponse(ApiModel):
    acks: list[SyncAck]


class PullChange(ApiModel):
    server_sequence: int
    event_type: str
    payload: dict
    received_at: datetime


class PullResponse(ApiModel):
    changes: list[PullChange]
    next_cursor: int
    has_more: bool

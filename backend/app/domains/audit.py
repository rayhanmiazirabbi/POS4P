from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from uuid6 import uuid7

from app.models.cross_cutting import AuditLog, OutboxEvent


def make_audit_log(
    organization_id: UUID,
    action: str,
    entity_type: str,
    request_id: str,
    *,
    store_id: UUID | None = None,
    actor_user_id: UUID | None = None,
    device_id: UUID | None = None,
    entity_id: UUID | None = None,
    before_data: dict | None = None,
    after_data: dict | None = None,
) -> AuditLog:
    return AuditLog(
        organization_id=organization_id,
        store_id=store_id,
        actor_user_id=actor_user_id,
        device_id=device_id,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        request_id=request_id,
        before_data=before_data,
        after_data=after_data,
        created_at=datetime.now(UTC),
    )


def make_outbox_event(organization_id: UUID, event_type: str, aggregate_type: str, aggregate_id: UUID, payload: dict) -> OutboxEvent:
    return OutboxEvent(
        id=uuid7(),
        organization_id=organization_id,
        event_type=event_type,
        aggregate_type=aggregate_type,
        aggregate_id=aggregate_id,
        payload=payload,
        created_at=datetime.now(UTC),
    )

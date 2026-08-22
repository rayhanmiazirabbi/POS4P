from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.context import RequestContext
from app.models import AuditLog, OutboxEvent
from app.security import utc_now


def record_audit(
    session: AsyncSession,
    context: RequestContext,
    *,
    action: str,
    entity_type: str,
    entity_id: UUID | None = None,
    request_id: str,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
) -> AuditLog:
    """Append an audit row inside the caller's transaction.

    Actor and tenant are taken from the validated ``RequestContext`` only -- never
    from client-supplied fields -- so an actor cannot be spoofed. Callers are
    responsible for passing redacted ``before``/``after`` summaries; secrets and
    prescription contents must not be included.
    """
    entry = AuditLog(
        organization_id=context.organization_id,
        store_id=context.store_id,
        actor_user_id=context.user_id,
        device_id=context.device_id,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        request_id=request_id,
        before_data=before,
        after_data=after,
        created_at=utc_now(),
    )
    session.add(entry)
    return entry


def enqueue_outbox(
    session: AsyncSession,
    *,
    organization_id: UUID,
    event_type: str,
    aggregate_type: str,
    aggregate_id: UUID,
    payload: dict[str, Any],
) -> OutboxEvent:
    """Stage an external side effect. The worker publishes it after commit."""
    event = OutboxEvent(
        organization_id=organization_id,
        event_type=event_type,
        aggregate_type=aggregate_type,
        aggregate_id=aggregate_id,
        payload=payload,
        created_at=utc_now(),
    )
    session.add(event)
    return event


REDACTED = "[redacted]"
_SENSITIVE_KEYS = frozenset(
    {
        "pin",
        "pin_hash",
        "password",
        "password_hash",
        "otp",
        "code",
        "challenge_hash",
        "access_token",
        "refresh_token",
        "refresh_token_hash",
        "secret",
        "authorization",
    }
)


def redact(data: dict[str, Any]) -> dict[str, Any]:
    """Strip known secret-bearing keys before an audit summary is persisted."""
    return {
        key: (REDACTED if key.lower() in _SENSITIVE_KEYS else value)
        for key, value in data.items()
    }

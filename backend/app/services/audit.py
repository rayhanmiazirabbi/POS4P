from __future__ import annotations

import hashlib
import hmac
import json
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.context import RequestContext
from app.models import AuditLog, OutboxEvent
from app.security import as_utc, utc_now


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
    entry.entry_hash = sign_entry(entry)
    session.add(entry)
    return entry


def _canonical_entry(entry: AuditLog) -> str:
    """The exact content a signature covers, stable across reloads."""
    parts = [
        str(entry.organization_id),
        str(entry.store_id or ""),
        str(entry.actor_user_id or ""),
        str(entry.device_id or ""),
        entry.action,
        entry.entity_type,
        str(entry.entity_id or ""),
        entry.request_id,
        json.dumps(entry.before_data, sort_keys=True, default=str),
        json.dumps(entry.after_data, sort_keys=True, default=str),
        as_utc(entry.created_at).isoformat(),
    ]
    return "|".join(parts)


def sign_entry(entry: AuditLog) -> str:
    secret = get_settings().audit_signing_secret.encode()
    return hmac.new(secret, _canonical_entry(entry).encode(), hashlib.sha256).hexdigest()


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


# --- owner audit view (phase six hardening) -----------------------------------


def _log_scope(
    context: RequestContext,
    *,
    action: str | None,
    entity_type: str | None,
    entity_id: UUID | None,
    actor_user_id: UUID | None,
    date_from: datetime | None,
    date_to: datetime | None,
    q: str | None,
) -> list[Any]:
    """Filters for the searchable owner view, always tenant-scoped.

    ``action`` is a prefix match (``sale.`` finds the whole sale family) and
    ``q`` is an explicit substring search over action and entity type only --
    never the before/after payloads, which may contain customer-identifying
    detail that should not be freely greppable.
    """
    scope: list[Any] = [AuditLog.organization_id == context.organization_id]
    if action:
        scope.append(AuditLog.action.like(f"{action}%"))
    if entity_type:
        scope.append(AuditLog.entity_type == entity_type)
    if entity_id is not None:
        scope.append(AuditLog.entity_id == entity_id)
    if actor_user_id is not None:
        scope.append(AuditLog.actor_user_id == actor_user_id)
    if date_from is not None:
        scope.append(AuditLog.created_at >= date_from)
    if date_to is not None:
        scope.append(AuditLog.created_at <= date_to)
    if q:
        needle = f"%{q.lower()}%"
        scope.append(or_(func.lower(AuditLog.action).like(needle), func.lower(AuditLog.entity_type).like(needle)))
    return scope


async def search_logs(
    session: AsyncSession,
    context: RequestContext,
    *,
    action: str | None = None,
    entity_type: str | None = None,
    entity_id: UUID | None = None,
    actor_user_id: UUID | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    q: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[AuditLog], int]:
    scope = _log_scope(
        context,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        actor_user_id=actor_user_id,
        date_from=date_from,
        date_to=date_to,
        q=q,
    )
    total = await session.scalar(select(func.count()).select_from(AuditLog).where(*scope))
    rows = list(
        await session.scalars(
            select(AuditLog)
            .where(*scope)
            .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
            .limit(limit)
            .offset(offset)
        )
    )
    return rows, int(total or 0)


#: Export ceiling so one careless "download everything" cannot pin the worker.
EXPORT_ROW_CAP = 5000


async def export_logs(session: AsyncSession, context: RequestContext, filters) -> list[AuditLog]:
    scope = _log_scope(
        context,
        action=filters.action,
        entity_type=filters.entity_type,
        entity_id=filters.entity_id,
        actor_user_id=filters.actor_user_id,
        date_from=filters.date_from,
        date_to=filters.date_to,
        q=filters.q,
    )
    return list(
        await session.scalars(
            select(AuditLog)
            .where(*scope)
            .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
            .limit(EXPORT_ROW_CAP)
        )
    )


# --- tamper evidence and retention (phase six hardening) -----------------------


async def verify_signatures(
    session: AsyncSession, context: RequestContext
) -> list[UUID]:
    """Ids of this tenant's audit rows whose stored signature no longer matches.

    A row signed ``None`` predates the signing scheme and is reported as
    unsigned rather than silently trusted.
    """
    rows = await session.scalars(
        select(AuditLog)
        .where(AuditLog.organization_id == context.organization_id)
        .order_by(AuditLog.created_at.asc(), AuditLog.id.asc())
    )
    return [row.id for row in rows if row.entry_hash != sign_entry(row)]


async def prune_expired(session: AsyncSession, context: RequestContext) -> int:
    """Delete this tenant's entries past the retention window; returns the count."""
    cutoff = utc_now() - timedelta(days=get_settings().audit_retention_days)
    result = await session.execute(
        delete(AuditLog).where(
            AuditLog.organization_id == context.organization_id,
            AuditLog.created_at < cutoff,
        )
    )
    await session.commit()
    return int(result.rowcount or 0)

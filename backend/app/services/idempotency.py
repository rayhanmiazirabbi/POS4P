from __future__ import annotations

import hashlib
import json
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.errors import IdempotencyConflict
from app.models import IdempotencyRecord
from app.security import utc_now


def request_fingerprint(payload: Any) -> str:
    """Stable hash of a request body so a replayed key with a different body is rejected."""
    encoded = json.dumps(payload, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()


async def replay(
    session: AsyncSession, organization_id: UUID, key: str, payload: Any
) -> dict[str, Any] | None:
    """Return the stored response when ``key`` was already used for this exact request.

    Raises ``IdempotencyConflict`` when the same key arrives with a different body,
    which is a client bug rather than a safe retry.
    """
    record = await session.scalar(
        select(IdempotencyRecord).where(
            IdempotencyRecord.organization_id == organization_id,
            IdempotencyRecord.key == key,
        )
    )
    if record is None:
        return None
    if record.request_hash != request_fingerprint(payload):
        raise IdempotencyConflict("Idempotency key was reused with a different request body")
    return record.response_body


def remember(
    session: AsyncSession,
    organization_id: UUID,
    key: str,
    payload: Any,
    *,
    response_status: int,
    response_body: dict[str, Any],
) -> IdempotencyRecord:
    """Persist the outcome in the same transaction as the effect it describes."""
    record = IdempotencyRecord(
        organization_id=organization_id,
        key=key,
        request_hash=request_fingerprint(payload),
        response_status=response_status,
        response_body=response_body,
        created_at=utc_now(),
    )
    session.add(record)
    return record

from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.context import RequestContext
from app.errors import IdempotencyConflict
from app.models import AuditLog, Role
from app.security import (
    generate_otp,
    hash_secret,
    sign_access_token,
    verify_access_token,
    verify_secret,
)
from app.services.audit import REDACTED, record_audit, redact
from app.services.idempotency import remember, replay


async def test_health_returns_request_id(client: AsyncClient) -> None:
    response = await client.get("/health", headers={"X-Request-ID": "req-1"})
    assert response.status_code == 200
    assert response.headers["X-Request-ID"] == "req-1"


async def test_unauthenticated_request_uses_shared_error_shape(client: AsyncClient) -> None:
    response = await client.get("/users/me")
    assert response.status_code in (401, 404)
    if response.status_code == 401:
        body = response.json()
        assert body["code"] == "UNAUTHORIZED"
        assert "requestId" in body


async def test_access_token_round_trip() -> None:
    token = sign_access_token({"sub": "abc", "org": "def"})
    claims = verify_access_token(token)
    assert claims is not None
    assert claims["sub"] == "abc"


async def test_tampered_access_token_is_rejected() -> None:
    token = sign_access_token({"sub": "abc"})
    body, _, signature = token.partition(".")
    assert verify_access_token(f"{body}x.{signature}") is None
    assert verify_access_token("garbage") is None


async def test_expired_access_token_is_rejected() -> None:
    assert verify_access_token(sign_access_token({"sub": "abc"}, expires_in_minutes=-1)) is None


async def test_pin_hash_is_not_reversible() -> None:
    hashed = hash_secret("1234")
    assert "1234" not in hashed
    assert verify_secret(hashed, "1234")
    assert not verify_secret(hashed, "9999")
    assert not verify_secret(None, "1234")


async def test_generated_otp_is_numeric() -> None:
    otp = generate_otp()
    assert len(otp) == 6 and otp.isdigit()


async def test_audit_rows_are_append_only(session: AsyncSession, tenant: dict[str, Any]) -> None:
    context = RequestContext(
        organization_id=tenant["organization"].id,
        user_id=tenant["owner"].id,
        role=Role.OWNER,
        store_id=tenant["store"].id,
    )
    entry = record_audit(
        session, context, action="store.updated", entity_type="store", request_id="req-1"
    )
    await session.commit()

    entry.action = "tampered"
    with pytest.raises(ValueError, match="append-only"):
        await session.commit()
    session.expunge_all()
    await session.rollback()

    reloaded = await session.get(AuditLog, entry.id)
    assert reloaded is not None
    assert reloaded.action == "store.updated"


async def test_redact_strips_secret_keys() -> None:
    assert redact({"pin": "1234", "displayName": "A"}) == {"pin": REDACTED, "displayName": "A"}


async def test_idempotent_replay_returns_stored_response(
    session: AsyncSession, tenant: dict[str, Any]
) -> None:
    organization_id = tenant["organization"].id
    payload = {"amount": "10.00"}
    assert await replay(session, organization_id, "key-0123456789abcdef", payload) is None

    remember(
        session,
        organization_id,
        "key-0123456789abcdef",
        payload,
        response_status=201,
        response_body={"id": "1"},
    )
    await session.commit()

    assert await replay(session, organization_id, "key-0123456789abcdef", payload) == {"id": "1"}

    with pytest.raises(IdempotencyConflict):
        await replay(session, organization_id, "key-0123456789abcdef", {"amount": "99.00"})

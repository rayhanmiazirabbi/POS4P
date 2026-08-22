from __future__ import annotations

from datetime import timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import AuditLog, AuthChallenge, Device, DeviceStatus, RecordStatus, Role
from app.models import Session as SessionModel
from app.security import hash_token, utc_now
from app.services.audit import REDACTED
from tests.conftest import access_token_for

OWNER_PHONE = "+8801700000001"
OWNER_PIN = "1234"


# --- helpers ---------------------------------------------------------------


async def _issue_code(client: AsyncClient, phone: str, purpose: str = "login") -> tuple[str, str]:
    """Request an OTP and return ``(challengeId, code)``.

    The plaintext code is only knowable here because non-production echoes it; the
    tests use that instead of stubbing the (not yet wired) SMS provider.
    """
    response = await client.post("/auth/otp/request", json={"phone": phone, "purpose": purpose})
    assert response.status_code == 200, response.text
    body = response.json()["data"]
    assert body["devCode"] is not None
    return body["challengeId"], body["devCode"]


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _audit_actions(session: AsyncSession, action: str) -> list[AuditLog]:
    return list(await session.scalars(select(AuditLog).where(AuditLog.action == action)))


# --- OTP issuance ----------------------------------------------------------


async def test_otp_request_does_not_disclose_whether_the_account_exists(
    client: AsyncClient, tenant: dict[str, Any]
) -> None:
    """Byte-identical response shapes; otherwise the endpoint enumerates customers."""
    known = await client.post("/auth/otp/request", json={"phone": OWNER_PHONE})
    unknown = await client.post("/auth/otp/request", json={"phone": "+8801999999999"})
    assert known.status_code == unknown.status_code == 200
    assert sorted(known.json()["data"]) == sorted(unknown.json()["data"])


async def test_otp_is_stored_only_as_a_hash(client: AsyncClient, session: AsyncSession) -> None:
    challenge_id, code = await _issue_code(client, "+8801711111111")
    challenge = await session.get(AuthChallenge, UUID(challenge_id))
    assert challenge is not None
    assert code not in challenge.challenge_hash
    assert challenge.challenge_hash.startswith("$argon2")


async def test_otp_request_is_rate_limited_per_destination(client: AsyncClient) -> None:
    limit = get_settings().otp_max_requests_per_window
    for _ in range(limit):
        assert (
            await client.post("/auth/otp/request", json={"phone": "+8801722222222"})
        ).status_code == 200
    blocked = await client.post("/auth/otp/request", json={"phone": "+8801722222222"})
    assert blocked.status_code == 429
    assert blocked.json()["code"] == "RATE_LIMITED"
    # The limit is per destination, so an unrelated number is unaffected.
    assert (
        await client.post("/auth/otp/request", json={"phone": "+8801733333333"})
    ).status_code == 200


async def test_production_never_echoes_the_code(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PHARMACY_ENVIRONMENT", "production")
    get_settings.cache_clear()
    try:
        response = await client.post("/auth/otp/request", json={"phone": "+8801744444444"})
        assert response.status_code == 200
        assert response.json()["data"]["devCode"] is None
    finally:
        get_settings.cache_clear()


async def test_phone_is_normalized_before_a_challenge_is_stored(
    client: AsyncClient, session: AsyncSession
) -> None:
    """``01700-000000`` and ``+8801700000000`` are one person, so one destination."""
    await _issue_code(client, "01755-000000")
    stored = await session.scalar(
        select(AuthChallenge.destination).order_by(AuthChallenge.created_at.desc())
    )
    assert stored == "+8801755000000"


# --- OTP verification ------------------------------------------------------


async def test_otp_verification_bootstraps_a_user_without_a_tenant(
    client: AsyncClient, session: AsyncSession
) -> None:
    """A brand-new owner authenticates before the organization they will create exists."""
    challenge_id, code = await _issue_code(client, "+8801766000000", purpose="signup")
    response = await client.post(
        "/auth/otp/verify",
        json={"challengeId": challenge_id, "code": code, "displayName": "New Owner"},
    )
    assert response.status_code == 200, response.text
    body = response.json()["data"]
    assert body["requiresOrganization"] is True
    assert body["organizationId"] is None
    assert body["role"] is None
    assert body["organizations"] == []
    # The token still authenticates the organization-create call.
    created = await client.post(
        "/organizations",
        json={"name": "Bootstrap Pharmacy"},
        headers=_bearer(body["accessToken"]),
    )
    assert created.status_code == 201, created.text


async def test_otp_verification_auto_selects_a_single_membership(
    client: AsyncClient, tenant: dict[str, Any]
) -> None:
    challenge_id, code = await _issue_code(client, OWNER_PHONE)
    response = await client.post(
        "/auth/otp/verify", json={"challengeId": challenge_id, "code": code}
    )
    assert response.status_code == 200, response.text
    body = response.json()["data"]
    assert body["requiresOrganization"] is False
    assert body["organizationId"] == str(tenant["organization"].id)
    assert body["storeId"] == str(tenant["store"].id)
    assert body["role"] == Role.OWNER.value
    assert len(body["organizations"]) == 1


async def test_replayed_otp_is_rejected(client: AsyncClient, tenant: dict[str, Any]) -> None:
    """A consumed challenge must not mint a second session even with the right code."""
    challenge_id, code = await _issue_code(client, OWNER_PHONE)
    first = await client.post("/auth/otp/verify", json={"challengeId": challenge_id, "code": code})
    assert first.status_code == 200
    replay = await client.post("/auth/otp/verify", json={"challengeId": challenge_id, "code": code})
    assert replay.status_code == 401
    assert replay.json()["code"] == "UNAUTHORIZED"


async def test_expired_otp_is_rejected(
    client: AsyncClient, session: AsyncSession, tenant: dict[str, Any]
) -> None:
    challenge_id, code = await _issue_code(client, OWNER_PHONE)
    challenge = await session.get(AuthChallenge, UUID(challenge_id))
    assert challenge is not None
    challenge.expires_at = utc_now() - timedelta(seconds=1)
    await session.commit()
    response = await client.post(
        "/auth/otp/verify", json={"challengeId": challenge_id, "code": code}
    )
    assert response.status_code == 401


async def test_otp_brute_force_exhausts_the_challenge(
    client: AsyncClient, tenant: dict[str, Any]
) -> None:
    """Guessing costs one challenge per ``otp_max_attempts`` tries, not unlimited tries."""
    challenge_id, code = await _issue_code(client, OWNER_PHONE)
    wrong = "000000" if code != "000000" else "111111"
    for _ in range(get_settings().otp_max_attempts):
        assert (
            await client.post(
                "/auth/otp/verify", json={"challengeId": challenge_id, "code": wrong}
            )
        ).status_code == 401
    locked = await client.post(
        "/auth/otp/verify", json={"challengeId": challenge_id, "code": wrong}
    )
    assert locked.status_code == 429
    # Even the correct code no longer works once the challenge is exhausted.
    assert (
        await client.post("/auth/otp/verify", json={"challengeId": challenge_id, "code": code})
    ).status_code == 429


async def test_unknown_challenge_id_is_rejected(client: AsyncClient) -> None:
    response = await client.post(
        "/auth/otp/verify", json={"challengeId": str(uuid4()), "code": "123456"}
    )
    assert response.status_code == 401


async def test_otp_login_audit_never_records_the_code(
    client: AsyncClient, session: AsyncSession, tenant: dict[str, Any]
) -> None:
    challenge_id, code = await _issue_code(client, OWNER_PHONE)
    await client.post("/auth/otp/verify", json={"challengeId": challenge_id, "code": code})
    entries = await _audit_actions(session, "auth.otp_login")
    assert len(entries) == 1
    serialized = str(entries[0].after_data)
    assert code not in serialized
    assert "challenge_hash" not in serialized


# --- staff PIN -------------------------------------------------------------


async def test_pin_login_issues_a_scoped_session(
    client: AsyncClient, tenant: dict[str, Any]
) -> None:
    response = await client.post(
        "/auth/pin/login",
        json={
            "phone": OWNER_PHONE,
            "pin": OWNER_PIN,
            "organizationId": str(tenant["organization"].id),
            "storeId": str(tenant["store"].id),
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()["data"]
    assert body["role"] == Role.OWNER.value
    assert body["storeId"] == str(tenant["store"].id)
    assert "pin" not in response.text.lower().replace("pinset", "")


async def test_failed_pin_login_is_indistinguishable_from_an_unknown_account(
    client: AsyncClient, tenant: dict[str, Any]
) -> None:
    """Same status, code, and message -- otherwise the endpoint is an account oracle."""
    organization_id = str(tenant["organization"].id)
    wrong_pin = await client.post(
        "/auth/pin/login",
        json={"phone": OWNER_PHONE, "pin": "9999", "organizationId": organization_id},
    )
    unknown_phone = await client.post(
        "/auth/pin/login",
        json={"phone": "+8801988888888", "pin": "9999", "organizationId": organization_id},
    )
    non_member = await client.post(
        "/auth/pin/login",
        json={"phone": OWNER_PHONE, "pin": OWNER_PIN, "organizationId": str(uuid4())},
    )
    for response in (wrong_pin, unknown_phone, non_member):
        assert response.status_code == 401
        assert response.json()["code"] == "UNAUTHORIZED"
    assert wrong_pin.json()["message"] == unknown_phone.json()["message"]
    assert non_member.json()["message"] == wrong_pin.json()["message"]


async def test_pin_brute_force_locks_the_account(
    client: AsyncClient, tenant: dict[str, Any]
) -> None:
    organization_id = str(tenant["organization"].id)
    payload = {"phone": OWNER_PHONE, "pin": "9999", "organizationId": organization_id}
    for _ in range(get_settings().pin_max_attempts):
        assert (await client.post("/auth/pin/login", json=payload)).status_code == 401
    locked = await client.post("/auth/pin/login", json=payload)
    assert locked.status_code == 429
    # The lockout holds even for the correct PIN, which is what makes it a lockout.
    correct = await client.post(
        "/auth/pin/login",
        json={"phone": OWNER_PHONE, "pin": OWNER_PIN, "organizationId": organization_id},
    )
    assert correct.status_code == 429


async def test_successful_pin_login_clears_earlier_failures(
    client: AsyncClient, session: AsyncSession, tenant: dict[str, Any]
) -> None:
    organization_id = str(tenant["organization"].id)
    await client.post(
        "/auth/pin/login",
        json={"phone": OWNER_PHONE, "pin": "9999", "organizationId": organization_id},
    )
    assert (
        await client.post(
            "/auth/pin/login",
            json={"phone": OWNER_PHONE, "pin": OWNER_PIN, "organizationId": organization_id},
        )
    ).status_code == 200
    await session.refresh(tenant["owner"])
    assert tenant["owner"].pin_attempts == 0
    assert tenant["owner"].pin_locked_until is None


async def test_deactivated_user_cannot_log_in(
    client: AsyncClient, session: AsyncSession, tenant: dict[str, Any]
) -> None:
    tenant["owner"].status = RecordStatus.INACTIVE
    await session.commit()
    response = await client.post(
        "/auth/pin/login",
        json={
            "phone": OWNER_PHONE,
            "pin": OWNER_PIN,
            "organizationId": str(tenant["organization"].id),
        },
    )
    assert response.status_code == 401


async def test_pin_login_rejects_a_store_from_another_tenant(
    client: AsyncClient,
    session: AsyncSession,
    tenant: dict[str, Any],
    make_organization: Any,
    make_store: Any,
) -> None:
    """Reported as missing, not forbidden, so the id cannot be confirmed to exist."""
    other = await make_organization(name="Rival", slug="rival")
    foreign_store = await make_store(other, name="Rival Branch", code="RIV")
    await session.commit()
    response = await client.post(
        "/auth/pin/login",
        json={
            "phone": OWNER_PHONE,
            "pin": OWNER_PIN,
            "organizationId": str(tenant["organization"].id),
            "storeId": str(foreign_store.id),
        },
    )
    assert response.status_code == 404
    assert response.json()["code"] == "NOT_FOUND"


async def test_cashier_cannot_select_a_store_they_are_not_assigned_to(
    client: AsyncClient,
    session: AsyncSession,
    tenant: dict[str, Any],
    make_user: Any,
    make_membership: Any,
    make_store: Any,
) -> None:
    cashier = await make_user(phone="+8801700000009", display_name="Cashier", pin="4321")
    await make_membership(tenant["organization"], cashier, Role.CASHIER)
    second = await make_store(tenant["organization"], name="Annex", code="ANNEX")
    await session.commit()
    response = await client.post(
        "/auth/pin/login",
        json={
            "phone": "+8801700000009",
            "pin": "4321",
            "organizationId": str(tenant["organization"].id),
            "storeId": str(second.id),
        },
    )
    assert response.status_code == 403
    assert response.json()["code"] == "FORBIDDEN"


# --- refresh and rotation --------------------------------------------------


async def test_refresh_rotates_the_token(client: AsyncClient, tenant: dict[str, Any]) -> None:
    first = await client.post("/auth/refresh", json={"refreshToken": tenant["refresh_token"]})
    assert first.status_code == 200, first.text
    rotated = first.json()["data"]["refreshToken"]
    assert rotated != tenant["refresh_token"]
    assert (await client.post("/auth/refresh", json={"refreshToken": rotated})).status_code == 200


async def test_replaying_a_rotated_refresh_token_revokes_the_session(
    client: AsyncClient, session: AsyncSession, tenant: dict[str, Any]
) -> None:
    """Reuse means the old token leaked, so the safe response is to end the session."""
    original = tenant["refresh_token"]
    rotated = (await client.post("/auth/refresh", json={"refreshToken": original})).json()["data"][
        "refreshToken"
    ]

    replay = await client.post("/auth/refresh", json={"refreshToken": original})
    assert replay.status_code == 401

    await session.refresh(tenant["session"])
    assert tenant["session"].revoked_at is not None
    # The token that replaced it is dead too, because the session itself is gone.
    assert (await client.post("/auth/refresh", json={"refreshToken": rotated})).status_code == 401


async def test_unknown_refresh_token_is_rejected(client: AsyncClient) -> None:
    response = await client.post("/auth/refresh", json={"refreshToken": "x" * 40})
    assert response.status_code == 401


async def test_expired_session_cannot_refresh(
    client: AsyncClient, session: AsyncSession, tenant: dict[str, Any]
) -> None:
    tenant["session"].expires_at = utc_now() - timedelta(minutes=1)
    await session.commit()
    response = await client.post("/auth/refresh", json={"refreshToken": tenant["refresh_token"]})
    assert response.status_code == 401


async def test_deactivated_user_cannot_refresh(
    client: AsyncClient, session: AsyncSession, tenant: dict[str, Any]
) -> None:
    tenant["owner"].status = RecordStatus.INACTIVE
    await session.commit()
    response = await client.post("/auth/refresh", json={"refreshToken": tenant["refresh_token"]})
    assert response.status_code == 401


async def test_refresh_denied_after_membership_is_withdrawn(
    client: AsyncClient, session: AsyncSession, tenant: dict[str, Any]
) -> None:
    membership = await session.scalar(
        select(__import__("app.models", fromlist=["OrganizationUser"]).OrganizationUser)
    )
    assert membership is not None
    membership.active = False
    await session.commit()
    response = await client.post("/auth/refresh", json={"refreshToken": tenant["refresh_token"]})
    assert response.status_code == 403


# --- session lifecycle -----------------------------------------------------


async def test_logout_revokes_only_the_calling_session(
    client: AsyncClient, session: AsyncSession, tenant: dict[str, Any], auth_headers: Any
) -> None:
    headers = auth_headers(tenant)
    other = SessionModel(
        user_id=tenant["owner"].id,
        organization_id=tenant["organization"].id,
        store_id=tenant["store"].id,
        refresh_token_hash=hash_token("second-device-token"),
        expires_at=utc_now() + timedelta(days=1),
    )
    session.add(other)
    await session.commit()

    response = await client.post("/auth/logout", headers=headers)
    assert response.status_code == 200, response.text
    assert response.json()["data"]["revokedSessionIds"] == [str(tenant["session"].id)]

    # The revoked session's access token stops working immediately.
    assert (await client.get("/auth/me", headers=headers)).status_code == 401
    await session.refresh(other)
    assert other.revoked_at is None


async def test_session_list_marks_the_current_session(
    client: AsyncClient, tenant: dict[str, Any], auth_headers: Any
) -> None:
    response = await client.get("/auth/sessions", headers=auth_headers(tenant))
    assert response.status_code == 200, response.text
    rows = response.json()["data"]
    assert [row["current"] for row in rows] == [True]
    assert rows[0]["id"] == str(tenant["session"].id)


async def test_owner_may_revoke_a_colleagues_session(
    client: AsyncClient,
    session: AsyncSession,
    tenant: dict[str, Any],
    auth_headers: Any,
    make_user: Any,
    make_membership: Any,
) -> None:
    cashier = await make_user(phone="+8801700000010", display_name="Cashier", pin="4321")
    await make_membership(tenant["organization"], cashier, Role.CASHIER, tenant["store"])
    victim = SessionModel(
        user_id=cashier.id,
        organization_id=tenant["organization"].id,
        store_id=tenant["store"].id,
        refresh_token_hash=hash_token("cashier-token"),
        expires_at=utc_now() + timedelta(days=1),
    )
    session.add(victim)
    await session.commit()

    response = await client.post(
        f"/auth/sessions/{victim.id}/revoke", headers=auth_headers(tenant)
    )
    assert response.status_code == 200, response.text
    await session.refresh(victim)
    assert victim.revoked_at is not None


async def test_cashier_cannot_revoke_another_users_session(
    client: AsyncClient,
    session: AsyncSession,
    tenant: dict[str, Any],
    make_user: Any,
    make_membership: Any,
) -> None:
    cashier = await make_user(phone="+8801700000011", display_name="Cashier", pin="4321")
    await make_membership(tenant["organization"], cashier, Role.CASHIER, tenant["store"])
    cashier_session = SessionModel(
        user_id=cashier.id,
        organization_id=tenant["organization"].id,
        store_id=tenant["store"].id,
        refresh_token_hash=hash_token("cashier-own-token"),
        expires_at=utc_now() + timedelta(days=1),
    )
    session.add(cashier_session)
    await session.commit()

    token = access_token_for(
        session_id=cashier_session.id,
        user_id=cashier.id,
        organization_id=tenant["organization"].id,
        role=Role.CASHIER,
        store_id=tenant["store"].id,
    )
    response = await client.post(
        f"/auth/sessions/{tenant['session'].id}/revoke", headers=_bearer(token)
    )
    assert response.status_code == 403
    # Their own session is still theirs to end.
    assert (
        await client.post(f"/auth/sessions/{cashier_session.id}/revoke", headers=_bearer(token))
    ).status_code == 200


async def test_session_from_another_tenant_is_reported_missing(
    client: AsyncClient,
    session: AsyncSession,
    tenant: dict[str, Any],
    auth_headers: Any,
    make_organization: Any,
    make_user: Any,
) -> None:
    """404 rather than 403: a foreign session id must not be confirmed to exist."""
    other = await make_organization(name="Rival", slug="rival")
    stranger = await make_user(phone="+8801700000012", display_name="Stranger")
    foreign = SessionModel(
        user_id=stranger.id,
        organization_id=other.id,
        refresh_token_hash=hash_token("foreign-token"),
        expires_at=utc_now() + timedelta(days=1),
    )
    session.add(foreign)
    await session.commit()

    response = await client.post(
        f"/auth/sessions/{foreign.id}/revoke", headers=auth_headers(tenant)
    )
    assert response.status_code == 404
    await session.refresh(foreign)
    assert foreign.revoked_at is None


# --- tenant and store selection -------------------------------------------


async def test_select_context_scopes_credentials_to_a_chosen_store(
    client: AsyncClient, tenant: dict[str, Any]
) -> None:
    challenge_id, code = await _issue_code(client, OWNER_PHONE)
    verified = (
        await client.post("/auth/otp/verify", json={"challengeId": challenge_id, "code": code})
    ).json()["data"]

    response = await client.post(
        "/auth/context",
        json={
            "organizationId": str(tenant["organization"].id),
            "storeId": str(tenant["store"].id),
        },
        headers=_bearer(verified["accessToken"]),
    )
    assert response.status_code == 200, response.text
    body = response.json()["data"]
    assert body["organizationId"] == str(tenant["organization"].id)
    assert body["storeId"] == str(tenant["store"].id)
    assert body["role"] == Role.OWNER.value

    me = await client.get("/auth/me", headers=_bearer(body["accessToken"]))
    assert me.status_code == 200, me.text
    assert me.json()["data"]["storeId"] == str(tenant["store"].id)


async def test_select_context_denies_an_organization_without_membership(
    client: AsyncClient,
    tenant: dict[str, Any],
    auth_headers: Any,
    make_organization: Any,
    session: AsyncSession,
) -> None:
    other = await make_organization(name="Rival", slug="rival")
    await session.commit()
    response = await client.post(
        "/auth/context",
        json={"organizationId": str(other.id)},
        headers=auth_headers(tenant),
    )
    assert response.status_code == 403
    assert response.json()["code"] == "FORBIDDEN"


async def test_me_reports_the_live_role_not_the_token_claim(
    client: AsyncClient, session: AsyncSession, tenant: dict[str, Any], auth_headers: Any
) -> None:
    """A token minted as owner must not keep conferring owner after a demotion."""
    from app.models import OrganizationUser

    headers = auth_headers(tenant, role=Role.OWNER)
    assert (await client.get("/auth/me", headers=headers)).json()["data"]["role"] == "owner"

    membership = await session.scalar(
        select(OrganizationUser).where(OrganizationUser.user_id == tenant["owner"].id)
    )
    assert membership is not None
    membership.role = Role.CASHIER
    await session.commit()

    assert (await client.get("/auth/me", headers=headers)).json()["data"]["role"] == "cashier"


async def test_me_requires_authentication(client: AsyncClient) -> None:
    response = await client.get("/auth/me")
    assert response.status_code == 401
    assert response.json()["code"] == "UNAUTHORIZED"


async def test_me_never_exposes_the_pin_hash(
    client: AsyncClient, tenant: dict[str, Any], auth_headers: Any
) -> None:
    response = await client.get("/auth/me", headers=auth_headers(tenant))
    assert response.status_code == 200
    assert response.json()["data"]["pinSet"] is True
    assert "pinHash" not in response.text
    assert "$argon2" not in response.text


# --- devices ---------------------------------------------------------------


async def test_device_registration_requires_store_context(
    client: AsyncClient, tenant: dict[str, Any], auth_headers: Any
) -> None:
    response = await client.post(
        "/auth/devices",
        json={"deviceKey": "counter-tablet-01", "name": "Counter Tablet"},
        headers=auth_headers(tenant, with_store=False),
    )
    assert response.status_code == 400
    assert response.json()["code"] == "STORE_CONTEXT_REQUIRED"


async def test_cashier_cannot_authorize_a_device(
    client: AsyncClient,
    session: AsyncSession,
    tenant: dict[str, Any],
    make_user: Any,
    make_membership: Any,
) -> None:
    cashier = await make_user(phone="+8801700000013", display_name="Cashier")
    await make_membership(tenant["organization"], cashier, Role.CASHIER, tenant["store"])
    cashier_session = SessionModel(
        user_id=cashier.id,
        organization_id=tenant["organization"].id,
        store_id=tenant["store"].id,
        refresh_token_hash=hash_token("cashier-device-token"),
        expires_at=utc_now() + timedelta(days=1),
    )
    session.add(cashier_session)
    await session.commit()
    token = access_token_for(
        session_id=cashier_session.id,
        user_id=cashier.id,
        organization_id=tenant["organization"].id,
        role=Role.CASHIER,
        store_id=tenant["store"].id,
    )
    response = await client.post(
        "/auth/devices",
        json={"deviceKey": "counter-tablet-02", "name": "Counter Tablet"},
        headers=_bearer(token),
    )
    assert response.status_code == 403


async def test_revoking_a_device_cuts_its_sessions_immediately(
    client: AsyncClient, session: AsyncSession, tenant: dict[str, Any], auth_headers: Any
) -> None:
    registered = await client.post(
        "/auth/devices",
        json={"deviceKey": "counter-tablet-03", "name": "Counter Tablet"},
        headers=auth_headers(tenant),
    )
    assert registered.status_code == 201, registered.text
    device_id = UUID(registered.json()["data"]["id"])

    bound = SessionModel(
        user_id=tenant["owner"].id,
        organization_id=tenant["organization"].id,
        store_id=tenant["store"].id,
        device_id=device_id,
        refresh_token_hash=hash_token("device-bound-token"),
        expires_at=utc_now() + timedelta(days=1),
    )
    session.add(bound)
    await session.commit()
    device_token = access_token_for(
        session_id=bound.id,
        user_id=tenant["owner"].id,
        organization_id=tenant["organization"].id,
        store_id=tenant["store"].id,
        device_id=bound.device_id,
    )
    assert (await client.get("/auth/me", headers=_bearer(device_token))).status_code == 200

    revoked = await client.post(
        f"/auth/devices/{device_id}/revoke", headers=auth_headers(tenant)
    )
    assert revoked.status_code == 200, revoked.text
    assert revoked.json()["data"]["status"] == DeviceStatus.REVOKED.value

    # Both the session row and any still-valid access token are dead.
    await session.refresh(bound)
    assert bound.revoked_at is not None
    denied = await client.get("/auth/me", headers=_bearer(device_token))
    assert denied.status_code == 401


async def test_revoked_device_cannot_be_used_to_log_in(
    client: AsyncClient, session: AsyncSession, tenant: dict[str, Any], auth_headers: Any
) -> None:
    registered = await client.post(
        "/auth/devices",
        json={"deviceKey": "counter-tablet-04", "name": "Counter Tablet"},
        headers=auth_headers(tenant),
    )
    device_id = registered.json()["data"]["id"]
    await client.post(f"/auth/devices/{device_id}/revoke", headers=auth_headers(tenant))

    response = await client.post(
        "/auth/pin/login",
        json={
            "phone": OWNER_PHONE,
            "pin": OWNER_PIN,
            "organizationId": str(tenant["organization"].id),
            "storeId": str(tenant["store"].id),
            "device": {"deviceKey": "counter-tablet-04", "deviceName": "Counter Tablet"},
        },
    )
    assert response.status_code == 403
    assert response.json()["code"] == "FORBIDDEN"


async def test_re_registration_reactivates_a_revoked_device(
    client: AsyncClient, session: AsyncSession, tenant: dict[str, Any], auth_headers: Any
) -> None:
    """Bringing a wiped terminal back is an explicit owner action, not a login side effect."""
    first = await client.post(
        "/auth/devices",
        json={"deviceKey": "counter-tablet-05", "name": "Counter Tablet"},
        headers=auth_headers(tenant),
    )
    device_id = first.json()["data"]["id"]
    await client.post(f"/auth/devices/{device_id}/revoke", headers=auth_headers(tenant))

    again = await client.post(
        "/auth/devices",
        json={"deviceKey": "counter-tablet-05", "name": "Counter Tablet (rebuilt)"},
        headers=auth_headers(tenant),
    )
    assert again.status_code == 201, again.text
    body = again.json()["data"]
    assert body["id"] == device_id  # reuses the row instead of duplicating the key
    assert body["status"] == DeviceStatus.ACTIVE.value
    assert body["name"] == "Counter Tablet (rebuilt)"


async def test_login_registers_an_unseen_device_once(
    client: AsyncClient, session: AsyncSession, tenant: dict[str, Any]
) -> None:
    payload = {
        "phone": OWNER_PHONE,
        "pin": OWNER_PIN,
        "organizationId": str(tenant["organization"].id),
        "storeId": str(tenant["store"].id),
        "device": {"deviceKey": "walkup-tablet", "deviceName": "Walk-up Tablet"},
    }
    assert (await client.post("/auth/pin/login", json=payload)).status_code == 200
    assert (await client.post("/auth/pin/login", json=payload)).status_code == 200
    count = await session.scalar(
        select(func.count()).select_from(Device).where(Device.device_key == "walkup-tablet")
    )
    assert count == 1


async def test_device_from_another_tenant_is_reported_missing(
    client: AsyncClient,
    session: AsyncSession,
    tenant: dict[str, Any],
    auth_headers: Any,
    make_organization: Any,
    make_store: Any,
) -> None:
    other = await make_organization(name="Rival", slug="rival")
    foreign_store = await make_store(other, name="Rival Branch", code="RIV")
    foreign_device = Device(
        organization_id=other.id,
        store_id=foreign_store.id,
        device_key="rival-tablet",
        name="Rival Tablet",
        status=DeviceStatus.ACTIVE,
    )
    session.add(foreign_device)
    await session.commit()

    response = await client.post(
        f"/auth/devices/{foreign_device.id}/revoke", headers=auth_headers(tenant)
    )
    assert response.status_code == 404
    await session.refresh(foreign_device)
    assert foreign_device.status is DeviceStatus.ACTIVE


async def test_device_list_is_scoped_to_the_tenant(
    client: AsyncClient,
    session: AsyncSession,
    tenant: dict[str, Any],
    auth_headers: Any,
    make_organization: Any,
    make_store: Any,
) -> None:
    other = await make_organization(name="Rival", slug="rival")
    foreign_store = await make_store(other, name="Rival Branch", code="RIV")
    session.add(
        Device(
            organization_id=other.id,
            store_id=foreign_store.id,
            device_key="rival-tablet",
            name="Rival Tablet",
            status=DeviceStatus.ACTIVE,
        )
    )
    await session.commit()
    await client.post(
        "/auth/devices",
        json={"deviceKey": "our-tablet", "name": "Our Tablet"},
        headers=auth_headers(tenant),
    )

    response = await client.get("/auth/devices", headers=auth_headers(tenant))
    assert response.status_code == 200
    keys = [row["deviceKey"] for row in response.json()["data"]]
    assert keys == ["our-tablet"]


# --- audit -----------------------------------------------------------------


async def test_auth_mutations_are_audited_without_secrets(
    client: AsyncClient, session: AsyncSession, tenant: dict[str, Any], auth_headers: Any
) -> None:
    headers = auth_headers(tenant)
    await client.post(
        "/auth/pin/login",
        json={
            "phone": OWNER_PHONE,
            "pin": OWNER_PIN,
            "organizationId": str(tenant["organization"].id),
        },
    )
    registered = await client.post(
        "/auth/devices",
        json={"deviceKey": "audited-tablet", "name": "Audited Tablet"},
        headers=headers,
    )
    await client.post(
        f"/auth/devices/{registered.json()['data']['id']}/revoke", headers=headers
    )
    await client.post("/auth/logout", headers=headers)

    actions = {
        row.action
        for row in await session.scalars(select(AuditLog))
    }
    assert {
        "auth.pin_login",
        "auth.device_registered",
        "auth.device_revoked",
        "auth.logout",
    } <= actions

    for row in await session.scalars(select(AuditLog)):
        serialized = str(row.before_data) + str(row.after_data)
        assert OWNER_PIN not in serialized or REDACTED in serialized
        assert "$argon2" not in serialized
        assert row.organization_id == tenant["organization"].id

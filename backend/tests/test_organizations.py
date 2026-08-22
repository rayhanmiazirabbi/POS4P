from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuditLog, Organization, OrganizationUser, RecordStatus, Role
from app.models import Session as SessionModel
from app.security import generate_token, hash_token, sign_access_token, utc_now


async def _auth_session(
    session: AsyncSession, user: Any, organization: Any, store: Any = None
) -> SessionModel:
    row = SessionModel(
        user_id=user.id,
        organization_id=organization.id,
        store_id=store.id if store is not None else None,
        refresh_token_hash=hash_token(generate_token()),
        expires_at=utc_now() + timedelta(days=30),
    )
    session.add(row)
    await session.flush()
    return row


def _bearer(
    auth_session: SessionModel,
    user: Any,
    organization: Any,
    *,
    store: Any = None,
    role: Role = Role.OWNER,
) -> dict[str, str]:
    claims: dict[str, Any] = {
        "sid": str(auth_session.id),
        "sub": str(user.id),
        "org": str(organization.id),
        "role": role.value,
    }
    if store is not None:
        claims["store"] = str(store.id)
    return {"Authorization": f"Bearer {sign_access_token(claims)}"}


async def _refetch(session: AsyncSession, organization_id: Any) -> Organization:
    """The API commits on its own session; bypass this session's identity map."""
    organization = await session.get(Organization, organization_id, populate_existing=True)
    assert organization is not None
    return organization


async def _audit_actions(session: AsyncSession, organization_id: Any) -> list[str]:
    return list(
        await session.scalars(
            select(AuditLog.action).where(AuditLog.organization_id == organization_id)
        )
    )


# --- create + owner bootstrap ---------------------------------------------


async def test_create_organization_bootstraps_owner_membership(
    client: AsyncClient, session: AsyncSession, tenant: dict[str, Any], auth_headers: Any
) -> None:
    response = await client.post(
        "/organizations",
        json={"name": "Green Life Pharmacy"},
        headers=auth_headers(tenant),
    )
    assert response.status_code == 201
    body = response.json()["data"]
    assert body["organization"]["slug"] == "green-life-pharmacy"
    assert body["organization"]["status"] == RecordStatus.ACTIVE.value
    assert body["role"] == Role.OWNER.value
    assert body["userId"] == str(tenant["owner"].id)

    created_id = UUID(body["organization"]["id"])
    membership = await session.scalar(
        select(OrganizationUser).where(OrganizationUser.organization_id == created_id)
    )
    assert membership is not None
    assert membership.user_id == tenant["owner"].id
    assert membership.role is Role.OWNER
    assert membership.active is True
    assert "organization.created" in await _audit_actions(session, created_id)


async def test_create_organization_applies_settings_defaults(
    client: AsyncClient, tenant: dict[str, Any], auth_headers: Any
) -> None:
    response = await client.post(
        "/organizations", json={"name": "Defaults Co"}, headers=auth_headers(tenant)
    )
    settings = response.json()["data"]["organization"]["settings"]
    assert settings == {
        "defaultTimezone": "Asia/Dhaka",
        "defaultCurrency": "BDT",
        "locale": "en-BD",
        "requirePinForDiscounts": True,
        "expiryAlertDays": 90,
        "lowStockThresholdDays": 14,
        "allowNegativeStock": False,
        "receiptFooter": None,
    }


async def test_create_organization_keeps_defaults_for_unsent_settings(
    client: AsyncClient, tenant: dict[str, Any], auth_headers: Any
) -> None:
    response = await client.post(
        "/organizations",
        json={"name": "Partial Co", "settings": {"expiryAlertDays": 30}},
        headers=auth_headers(tenant),
    )
    settings = response.json()["data"]["organization"]["settings"]
    assert settings["expiryAlertDays"] == 30
    assert settings["lowStockThresholdDays"] == 14


async def test_owner_bootstrap_is_atomic(
    client: AsyncClient,
    session: AsyncSession,
    tenant: dict[str, Any],
    auth_headers: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failure after the organization insert must leave no tenant and no membership."""

    def boom(*_: Any, **__: Any) -> None:
        raise RuntimeError("audit sink unavailable")

    monkeypatch.setattr("app.services.organizations.record_audit", boom)
    with pytest.raises(RuntimeError):
        await client.post(
            "/organizations", json={"name": "Ghost Pharmacy"}, headers=auth_headers(tenant)
        )

    assert (
        await session.scalar(select(Organization.id).where(Organization.slug == "ghost-pharmacy"))
        is None
    )
    memberships = list(
        await session.scalars(
            select(OrganizationUser.organization_id).where(
                OrganizationUser.user_id == tenant["owner"].id
            )
        )
    )
    assert memberships == [tenant["organization"].id]


async def test_create_organization_rejects_duplicate_slug(
    client: AsyncClient, tenant: dict[str, Any], auth_headers: Any
) -> None:
    response = await client.post(
        "/organizations",
        json={"name": "Other Name", "slug": tenant["organization"].slug},
        headers=auth_headers(tenant),
    )
    assert response.status_code == 409
    assert response.json()["code"] == "CONFLICT"


async def test_create_organization_rejects_invalid_slug(
    client: AsyncClient, tenant: dict[str, Any], auth_headers: Any
) -> None:
    response = await client.post(
        "/organizations",
        json={"name": "Bad Slug", "slug": "Not A Slug"},
        headers=auth_headers(tenant),
    )
    assert response.status_code == 422


async def test_create_organization_requires_authentication(client: AsyncClient) -> None:
    response = await client.post("/organizations", json={"name": "Anonymous"})
    assert response.status_code == 401
    assert response.json()["code"] == "UNAUTHORIZED"


async def test_create_organization_rejects_revoked_session(
    client: AsyncClient, session: AsyncSession, tenant: dict[str, Any], auth_headers: Any
) -> None:
    tenant["session"].revoked_at = utc_now()
    await session.commit()
    response = await client.post(
        "/organizations", json={"name": "Revoked Co"}, headers=auth_headers(tenant)
    )
    assert response.status_code == 401


# --- current organization context ------------------------------------------


async def test_current_organization_reports_validated_context(
    client: AsyncClient, tenant: dict[str, Any], auth_headers: Any
) -> None:
    response = await client.get("/organizations/current", headers=auth_headers(tenant))
    assert response.status_code == 200
    body = response.json()["data"]
    assert body["organization"]["id"] == str(tenant["organization"].id)
    assert body["role"] == Role.OWNER.value
    assert body["userId"] == str(tenant["owner"].id)
    assert body["storeId"] == str(tenant["store"].id)
    assert body["store"]["organizationId"] == str(tenant["organization"].id)
    assert body["settings"]["defaultCurrency"] == "BDT"


async def test_current_organization_without_store_context(
    client: AsyncClient, tenant: dict[str, Any], auth_headers: Any
) -> None:
    response = await client.get(
        "/organizations/current", headers=auth_headers(tenant, with_store=False)
    )
    body = response.json()["data"]
    assert body["storeId"] is None
    assert body["store"] is None


async def test_token_for_foreign_organization_is_denied(
    client: AsyncClient,
    session: AsyncSession,
    tenant: dict[str, Any],
    make_organization: Callable[..., Any],
) -> None:
    """The tenant is re-derived from live membership, so a forged ``org`` claim fails."""
    other = await make_organization(name="Rival Pharmacy", slug="rival-pharmacy")
    await session.commit()
    headers = _bearer(tenant["session"], tenant["owner"], other)
    response = await client.get("/organizations/current", headers=headers)
    assert response.status_code in (401, 403)
    assert "data" not in response.json()


async def test_forged_token_is_rejected(client: AsyncClient) -> None:
    response = await client.get(
        "/organizations/current", headers={"Authorization": "Bearer not-a-token"}
    )
    assert response.status_code == 401
    assert response.json()["code"] == "UNAUTHORIZED"


# --- profile and settings ---------------------------------------------------


async def test_settings_read_applies_defaults_to_legacy_rows(
    client: AsyncClient, tenant: dict[str, Any], auth_headers: Any
) -> None:
    """The fixture organization stores ``{}``; reads must still return usable values."""
    response = await client.get("/organizations/current/settings", headers=auth_headers(tenant))
    assert response.status_code == 200
    body = response.json()["data"]
    assert body["organizationId"] == str(tenant["organization"].id)
    assert body["settings"]["expiryAlertDays"] == 90


async def test_settings_update_merges_and_audits(
    client: AsyncClient, session: AsyncSession, tenant: dict[str, Any], auth_headers: Any
) -> None:
    response = await client.patch(
        "/organizations/current/settings",
        json={"allowNegativeStock": True, "receiptFooter": "Thank you"},
        headers=auth_headers(tenant),
    )
    assert response.status_code == 200
    settings = response.json()["data"]["settings"]
    assert settings["allowNegativeStock"] is True
    assert settings["receiptFooter"] == "Thank you"
    assert settings["expiryAlertDays"] == 90

    organization = await _refetch(session, tenant["organization"].id)
    assert organization.settings["allow_negative_stock"] is True
    assert "organization.settings_updated" in await _audit_actions(
        session, tenant["organization"].id
    )


async def test_settings_update_rejects_unknown_keys(
    client: AsyncClient, tenant: dict[str, Any], auth_headers: Any
) -> None:
    response = await client.patch(
        "/organizations/current/settings",
        json={"turboMode": True},
        headers=auth_headers(tenant),
    )
    assert response.status_code == 422
    assert response.json()["code"] == "VALIDATION_ERROR"


async def test_settings_update_rejects_unsupported_currency(
    client: AsyncClient, tenant: dict[str, Any], auth_headers: Any
) -> None:
    response = await client.patch(
        "/organizations/current/settings",
        json={"defaultCurrency": "XYZ"},
        headers=auth_headers(tenant),
    )
    assert response.status_code == 422


async def test_profile_update_renames_and_audits(
    client: AsyncClient, session: AsyncSession, tenant: dict[str, Any], auth_headers: Any
) -> None:
    response = await client.patch(
        "/organizations/current/profile",
        json={"name": "Renamed Pharmacy", "slug": "renamed-pharmacy"},
        headers=auth_headers(tenant),
    )
    assert response.status_code == 200
    assert response.json()["data"]["name"] == "Renamed Pharmacy"

    organization = await _refetch(session, tenant["organization"].id)
    assert organization.slug == "renamed-pharmacy"
    assert "organization.updated" in await _audit_actions(session, tenant["organization"].id)


async def test_profile_update_rejects_taken_slug(
    client: AsyncClient,
    session: AsyncSession,
    tenant: dict[str, Any],
    auth_headers: Any,
    make_organization: Callable[..., Any],
) -> None:
    await make_organization(name="Taken", slug="taken-slug")
    await session.commit()
    response = await client.patch(
        "/organizations/current/profile",
        json={"slug": "taken-slug"},
        headers=auth_headers(tenant),
    )
    assert response.status_code == 409


# --- tenant isolation and role enforcement ---------------------------------


async def test_settings_changes_do_not_leak_across_tenants(
    client: AsyncClient,
    session: AsyncSession,
    tenant: dict[str, Any],
    auth_headers: Any,
    make_organization: Callable[..., Any],
    make_user: Callable[..., Any],
    make_membership: Callable[..., Any],
) -> None:
    other_org = await make_organization(name="Rival", slug="rival")
    other_owner = await make_user(phone="+8801700000042", display_name="Rival Owner")
    await make_membership(other_org, other_owner, Role.OWNER)
    other_session = await _auth_session(session, other_owner, other_org)
    await session.commit()

    await client.patch(
        "/organizations/current/settings",
        json={"expiryAlertDays": 10},
        headers=auth_headers(tenant),
    )
    response = await client.get(
        "/organizations/current/settings",
        headers=_bearer(other_session, other_owner, other_org),
    )
    assert response.json()["data"]["organizationId"] == str(other_org.id)
    assert response.json()["data"]["settings"]["expiryAlertDays"] == 90


async def test_cashier_cannot_change_organization_settings(
    client: AsyncClient,
    session: AsyncSession,
    tenant: dict[str, Any],
    make_user: Callable[..., Any],
    make_membership: Callable[..., Any],
) -> None:
    cashier = await make_user(phone="+8801700000009", display_name="Cashier")
    await make_membership(tenant["organization"], cashier, Role.CASHIER, tenant["store"])
    cashier_session = await _auth_session(
        session, cashier, tenant["organization"], tenant["store"]
    )
    await session.commit()
    headers = _bearer(
        cashier_session,
        cashier,
        tenant["organization"],
        store=tenant["store"],
        role=Role.CASHIER,
    )

    assert (await client.get("/organizations/current", headers=headers)).status_code == 200
    forbidden = await client.patch(
        "/organizations/current/settings", json={"allowNegativeStock": True}, headers=headers
    )
    assert forbidden.status_code == 403
    assert forbidden.json()["code"] == "FORBIDDEN"
    assert (
        await client.patch(
            "/organizations/current/profile", json={"name": "Hijacked"}, headers=headers
        )
    ).status_code == 403


async def test_role_claim_cannot_escalate_privileges(
    client: AsyncClient,
    session: AsyncSession,
    tenant: dict[str, Any],
    make_user: Callable[..., Any],
    make_membership: Callable[..., Any],
) -> None:
    """The role is read from the membership row, so a token claiming ``owner`` is ignored."""
    cashier = await make_user(phone="+8801700000010", display_name="Sneaky Cashier")
    await make_membership(tenant["organization"], cashier, Role.CASHIER, tenant["store"])
    cashier_session = await _auth_session(
        session, cashier, tenant["organization"], tenant["store"]
    )
    await session.commit()
    headers = _bearer(
        cashier_session, cashier, tenant["organization"], store=tenant["store"], role=Role.OWNER
    )
    response = await client.patch(
        "/organizations/current/settings", json={"allowNegativeStock": True}, headers=headers
    )
    assert response.status_code == 403


async def test_deactivated_membership_loses_access(
    client: AsyncClient, session: AsyncSession, tenant: dict[str, Any], auth_headers: Any
) -> None:
    membership = await session.scalar(
        select(OrganizationUser).where(
            OrganizationUser.organization_id == tenant["organization"].id,
            OrganizationUser.user_id == tenant["owner"].id,
        )
    )
    assert membership is not None
    membership.active = False
    await session.commit()
    response = await client.get("/organizations/current", headers=auth_headers(tenant))
    assert response.status_code == 403


async def test_suspended_organization_is_readable_but_frozen(
    client: AsyncClient, session: AsyncSession, tenant: dict[str, Any], auth_headers: Any
) -> None:
    """A suspended subscription must not silently accept configuration changes."""
    organization = await _refetch(session, tenant["organization"].id)
    organization.status = RecordStatus.SUSPENDED
    await session.commit()

    readable = await client.get("/organizations/current/settings", headers=auth_headers(tenant))
    assert readable.status_code == 200

    for path, payload in (
        ("/organizations/current/settings", {"allowNegativeStock": True}),
        ("/organizations/current/profile", {"name": "Still Trading"}),
    ):
        blocked = await client.patch(path, json=payload, headers=auth_headers(tenant))
        assert blocked.status_code == 403
        assert blocked.json()["code"] == "FORBIDDEN"


async def test_missing_organization_row_is_reported_as_not_found(
    client: AsyncClient, session: AsyncSession, tenant: dict[str, Any]
) -> None:
    """A context pointing at a vanished tenant must 404 rather than fall back to any row."""
    from app.context import RequestContext
    from app.errors import NotFound
    from app.services.organizations import get_organization

    context = RequestContext(
        organization_id=uuid4(), user_id=tenant["owner"].id, role=Role.OWNER
    )
    with pytest.raises(NotFound):
        await get_organization(session, context)

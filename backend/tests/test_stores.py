from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.errors import Forbidden
from app.models import AuditLog, RecordStatus, Role, Store
from app.models import Session as SessionModel
from app.security import generate_token, hash_token, sign_access_token, utc_now
from app.services.stores import business_date, local_now, require_operational_store


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


async def _refetch(session: AsyncSession, store_id: Any) -> Store:
    """The API commits on its own session; bypass this session's identity map."""
    store = await session.get(Store, store_id, populate_existing=True)
    assert store is not None
    return store


async def _audit_actions(session: AsyncSession, store_id: Any) -> list[str]:
    return list(
        await session.scalars(select(AuditLog.action).where(AuditLog.store_id == store_id))
    )


async def _second_tenant(
    session: AsyncSession,
    make_organization: Callable[..., Any],
    make_user: Callable[..., Any],
    make_store: Callable[..., Any],
    make_membership: Callable[..., Any],
) -> dict[str, Any]:
    organization = await make_organization(name="Rival Pharmacy", slug="rival-pharmacy")
    owner = await make_user(phone="+8801799999999", display_name="Rival Owner")
    store = await make_store(organization, name="Rival Branch", code="RIVAL")
    await make_membership(organization, owner, Role.OWNER, store)
    auth_session = await _auth_session(session, owner, organization, store)
    await session.commit()
    return {
        "organization": organization,
        "owner": owner,
        "store": store,
        "session": auth_session,
    }


async def _staff(
    session: AsyncSession,
    tenant: dict[str, Any],
    make_user: Callable[..., Any],
    make_membership: Callable[..., Any],
    *,
    role: Role,
    phone: str,
    store: Any = None,
) -> dict[str, str]:
    user = await make_user(phone=phone, display_name=role.value)
    await make_membership(tenant["organization"], user, role, store)
    auth_session = await _auth_session(session, user, tenant["organization"], store)
    await session.commit()
    return _bearer(auth_session, user, tenant["organization"], store=store, role=role)


# --- create -----------------------------------------------------------------


async def test_create_store_inherits_organization_defaults(
    client: AsyncClient,
    session: AsyncSession,
    make_organization: Callable[..., Any],
    make_user: Callable[..., Any],
    make_membership: Callable[..., Any],
) -> None:
    organization = await make_organization(name="Fresh Co", slug="fresh-co")
    organization.settings = {"default_timezone": "Asia/Kolkata", "default_currency": "BDT"}
    owner = await make_user(phone="+8801711111111", display_name="Fresh Owner")
    await make_membership(organization, owner, Role.OWNER)
    auth_session = await _auth_session(session, owner, organization)
    await session.commit()

    response = await client.post(
        "/stores",
        json={"name": "Mirpur Branch"},
        headers=_bearer(auth_session, owner, organization),
    )
    assert response.status_code == 201
    body = response.json()["data"]
    assert body["organizationId"] == str(organization.id)
    assert body["code"] == "MAIN"
    assert body["timezone"] == "Asia/Kolkata"
    assert body["currency"] == "BDT"
    assert body["status"] == RecordStatus.ACTIVE.value
    assert body["settings"] == {
        "receiptHeader": None,
        "receiptFooter": None,
        "receiptLogo": None,
        "receiptBusinessName": None,
        "receiptAddress": None,
        "receiptPhone": None,
        "receiptEmail": None,
        "receiptTaxId": None,
        "receiptPaperWidthMm": 80,
        "receiptShowLogo": True,
        "receiptShowBusinessName": True,
        "receiptShowStoreName": True,
        "receiptShowContactDetails": True,
        "receiptShowHeader": True,
        "receiptShowReceiptNumber": True,
        "receiptShowDateTime": True,
        "receiptShowCustomer": True,
        "receiptShowCashier": True,
        "receiptShowItems": True,
        "receiptShowItemQuantity": True,
        "receiptShowUnitPrice": True,
        "receiptShowLineTotal": True,
        "receiptShowSubtotal": True,
        "receiptShowDiscounts": True,
        "receiptShowCharges": True,
        "receiptShowTotal": True,
        "receiptShowPayments": True,
        "receiptShowCashReceived": True,
        "receiptShowChangeDue": True,
        "receiptShowFooter": True,
        "businessDayCutoffHour": 0,
        "lowStockAlerts": True,
        "allowOfflineSales": True,
        "printReceiptByDefault": True,
    }
    assert "store.created" in await _audit_actions(session, UUID(body["id"]))


async def test_store_cannot_be_created_for_a_suspended_organization(
    client: AsyncClient,
    session: AsyncSession,
    make_organization: Callable[..., Any],
    make_user: Callable[..., Any],
    make_membership: Callable[..., Any],
) -> None:
    organization = await make_organization(
        name="Frozen Co", slug="frozen-co", status=RecordStatus.SUSPENDED
    )
    owner = await make_user(phone="+8801722222222", display_name="Frozen Owner")
    await make_membership(organization, owner, Role.OWNER)
    auth_session = await _auth_session(session, owner, organization)
    await session.commit()

    response = await client.post(
        "/stores",
        json={"name": "Blocked Branch"},
        headers=_bearer(auth_session, owner, organization),
    )
    assert response.status_code == 403
    assert response.json()["code"] == "FORBIDDEN"


async def test_create_store_is_limited_to_one_per_organization(
    client: AsyncClient, tenant: dict[str, Any], auth_headers: Any
) -> None:
    response = await client.post(
        "/stores", json={"name": "Second Branch", "code": "TWO"}, headers=auth_headers(tenant)
    )
    assert response.status_code == 409
    assert response.json()["code"] == "CONFLICT"


async def test_create_store_rejects_duplicate_code(
    client: AsyncClient, tenant: dict[str, Any], auth_headers: Any
) -> None:
    response = await client.post(
        "/stores", json={"name": "Clone", "code": "main"}, headers=auth_headers(tenant)
    )
    assert response.status_code == 409


async def test_create_store_rejects_unknown_timezone(
    client: AsyncClient, tenant: dict[str, Any], auth_headers: Any
) -> None:
    response = await client.post(
        "/stores",
        json={"name": "Nowhere", "code": "NOW", "timezone": "Mars/Olympus"},
        headers=auth_headers(tenant),
    )
    assert response.status_code == 422
    assert response.json()["code"] == "VALIDATION_ERROR"


async def test_cashier_cannot_create_a_store(
    client: AsyncClient,
    session: AsyncSession,
    tenant: dict[str, Any],
    make_user: Callable[..., Any],
    make_membership: Callable[..., Any],
) -> None:
    headers = await _staff(
        session,
        tenant,
        make_user,
        make_membership,
        role=Role.CASHIER,
        phone="+8801700000021",
        store=tenant["store"],
    )
    response = await client.post("/stores", json={"name": "Cashier Branch"}, headers=headers)
    assert response.status_code == 403
    assert response.json()["code"] == "FORBIDDEN"


# --- read + store context ---------------------------------------------------


async def test_current_store_is_resolved_from_the_token_context(
    client: AsyncClient, tenant: dict[str, Any], auth_headers: Any
) -> None:
    response = await client.get("/stores/current", headers=auth_headers(tenant))
    assert response.status_code == 200
    body = response.json()["data"]
    assert body["id"] == str(tenant["store"].id)
    assert body["organizationId"] == str(tenant["organization"].id)


async def test_current_store_requires_a_store_scoped_token(
    client: AsyncClient, tenant: dict[str, Any], auth_headers: Any
) -> None:
    response = await client.get("/stores/current", headers=auth_headers(tenant, with_store=False))
    assert response.status_code == 400
    assert response.json()["code"] == "STORE_CONTEXT_REQUIRED"


async def test_store_list_only_returns_the_callers_organization(
    client: AsyncClient,
    session: AsyncSession,
    tenant: dict[str, Any],
    auth_headers: Any,
    make_organization: Callable[..., Any],
    make_user: Callable[..., Any],
    make_store: Callable[..., Any],
    make_membership: Callable[..., Any],
) -> None:
    other = await _second_tenant(
        session, make_organization, make_user, make_store, make_membership
    )
    response = await client.get("/stores", headers=auth_headers(tenant))
    assert response.status_code == 200
    page = response.json()["data"]
    assert [item["id"] for item in page["items"]] == [str(tenant["store"].id)]
    assert str(other["store"].id) not in response.text


async def test_store_list_is_limited_to_assigned_branches_for_staff(
    client: AsyncClient,
    session: AsyncSession,
    tenant: dict[str, Any],
    make_user: Callable[..., Any],
    make_membership: Callable[..., Any],
) -> None:
    """A cashier without a ``store_users`` row must not see a branch it cannot enter."""
    headers = await _staff(
        session,
        tenant,
        make_user,
        make_membership,
        role=Role.CASHIER,
        phone="+8801700000022",
    )
    response = await client.get("/stores", headers=headers)
    assert response.status_code == 200
    assert response.json()["data"]["items"] == []


# --- cross-tenant isolation -------------------------------------------------


async def test_direct_object_access_to_another_tenants_store_is_denied(
    client: AsyncClient,
    session: AsyncSession,
    tenant: dict[str, Any],
    auth_headers: Any,
    make_organization: Callable[..., Any],
    make_user: Callable[..., Any],
    make_store: Callable[..., Any],
    make_membership: Callable[..., Any],
) -> None:
    other = await _second_tenant(
        session, make_organization, make_user, make_store, make_membership
    )
    foreign_id = other["store"].id
    headers = auth_headers(tenant)

    for method, path, payload in (
        ("get", f"/stores/{foreign_id}", None),
        ("get", f"/stores/{foreign_id}/settings", None),
        ("get", f"/stores/{foreign_id}/operating-status", None),
        ("patch", f"/stores/{foreign_id}", {"name": "Stolen"}),
        ("patch", f"/stores/{foreign_id}/settings", {"lowStockAlerts": False}),
        ("patch", f"/stores/{foreign_id}/operating-status", {"status": "suspended"}),
    ):
        response = await client.request(method, path, json=payload, headers=headers)
        assert response.status_code in (403, 404), (method, path)
        assert "Rival" not in response.text

    unchanged = await _refetch(session, foreign_id)
    assert unchanged.name == "Rival Branch"
    assert unchanged.status is RecordStatus.ACTIVE


async def test_unknown_store_id_is_not_found(
    client: AsyncClient, tenant: dict[str, Any], auth_headers: Any
) -> None:
    response = await client.get(f"/stores/{uuid4()}", headers=auth_headers(tenant))
    assert response.status_code == 404
    assert response.json()["code"] == "NOT_FOUND"


async def test_token_pinned_to_a_foreign_store_is_denied(
    client: AsyncClient,
    session: AsyncSession,
    tenant: dict[str, Any],
    make_organization: Callable[..., Any],
    make_user: Callable[..., Any],
    make_store: Callable[..., Any],
    make_membership: Callable[..., Any],
) -> None:
    """Switching stores by editing the token's ``store`` claim must fail closed."""
    other = await _second_tenant(
        session, make_organization, make_user, make_store, make_membership
    )
    headers = _bearer(
        tenant["session"], tenant["owner"], tenant["organization"], store=other["store"]
    )
    response = await client.get("/stores/current", headers=headers)
    assert response.status_code in (403, 404)
    assert "data" not in response.json()


async def test_staff_cannot_use_a_branch_they_are_not_assigned_to(
    client: AsyncClient,
    session: AsyncSession,
    tenant: dict[str, Any],
    make_user: Callable[..., Any],
    make_membership: Callable[..., Any],
) -> None:
    """Store context is re-checked against ``store_users`` on every scoped read."""
    cashier = await make_user(phone="+8801700000023", display_name="Unassigned")
    await make_membership(tenant["organization"], cashier, Role.CASHIER)
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
    response = await client.get("/stores/current", headers=headers)
    assert response.status_code == 403
    assert response.json()["code"] == "FORBIDDEN"


async def test_revoking_a_store_assignment_ends_access(
    client: AsyncClient,
    session: AsyncSession,
    tenant: dict[str, Any],
    make_user: Callable[..., Any],
    make_membership: Callable[..., Any],
) -> None:
    from app.models import StoreUser

    cashier = await make_user(phone="+8801700000024", display_name="Assigned")
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
    assert (await client.get("/stores/current", headers=headers)).status_code == 200

    assignment = await session.scalar(
        select(StoreUser).where(
            StoreUser.store_id == tenant["store"].id, StoreUser.user_id == cashier.id
        )
    )
    assert assignment is not None
    assignment.active = False
    await session.commit()
    assert (await client.get("/stores/current", headers=headers)).status_code == 403


# --- profile, timezone, currency -------------------------------------------


async def test_update_store_persists_timezone_and_audits(
    client: AsyncClient, session: AsyncSession, tenant: dict[str, Any], auth_headers: Any
) -> None:
    response = await client.patch(
        f"/stores/{tenant['store'].id}",
        json={"name": "Gulshan Branch", "timezone": "Asia/Kolkata"},
        headers=auth_headers(tenant),
    )
    assert response.status_code == 200
    assert response.json()["data"]["timezone"] == "Asia/Kolkata"

    store = await _refetch(session, tenant["store"].id)
    assert store.name == "Gulshan Branch"
    assert store.timezone == "Asia/Kolkata"
    assert "store.updated" in await _audit_actions(session, store.id)


async def test_stored_timezone_drives_the_business_date(
    client: AsyncClient, session: AsyncSession, tenant: dict[str, Any], auth_headers: Any
) -> None:
    """23:30 UTC is already the next day in Dhaka, so the trading day must roll over."""
    await client.patch(
        f"/stores/{tenant['store'].id}",
        json={"timezone": "Asia/Dhaka"},
        headers=auth_headers(tenant),
    )
    store = await _refetch(session, tenant["store"].id)
    moment = datetime(2026, 3, 1, 23, 30, tzinfo=UTC)
    assert local_now(store, moment=moment).hour == 5
    assert business_date(store, moment=moment).isoformat() == "2026-03-02"

    response = await client.get(
        f"/stores/{store.id}/operating-status", headers=auth_headers(tenant)
    )
    body = response.json()["data"]
    assert body["timezone"] == "Asia/Dhaka"
    assert body["localTime"].startswith(
        datetime.now(tz=ZoneInfo("Asia/Dhaka")).date().isoformat()
    )


async def test_business_date_honours_the_cutoff_hour(
    client: AsyncClient, session: AsyncSession, tenant: dict[str, Any], auth_headers: Any
) -> None:
    await client.patch(
        f"/stores/{tenant['store'].id}/settings",
        json={"businessDayCutoffHour": 6},
        headers=auth_headers(tenant),
    )
    store = await _refetch(session, tenant["store"].id)
    moment = datetime(2026, 3, 1, 23, 30, tzinfo=UTC)
    assert local_now(store, moment=moment).date().isoformat() == "2026-03-02"
    assert business_date(store, moment=moment).isoformat() == "2026-03-01"


async def test_update_store_rejects_unsupported_currency(
    client: AsyncClient, tenant: dict[str, Any], auth_headers: Any
) -> None:
    response = await client.patch(
        f"/stores/{tenant['store'].id}",
        json={"currency": "USD"},
        headers=auth_headers(tenant),
    )
    assert response.status_code == 422
    assert response.json()["code"] == "VALIDATION_ERROR"


async def test_update_store_accepts_the_supported_currency(
    client: AsyncClient, tenant: dict[str, Any], auth_headers: Any
) -> None:
    response = await client.patch(
        f"/stores/{tenant['store'].id}",
        json={"currency": "bdt"},
        headers=auth_headers(tenant),
    )
    assert response.status_code == 200
    assert response.json()["data"]["currency"] == "BDT"


# --- settings ---------------------------------------------------------------


async def test_store_settings_read_applies_defaults(
    client: AsyncClient, tenant: dict[str, Any], auth_headers: Any
) -> None:
    response = await client.get(
        f"/stores/{tenant['store'].id}/settings", headers=auth_headers(tenant)
    )
    assert response.status_code == 200
    body = response.json()["data"]
    assert body["storeId"] == str(tenant["store"].id)
    assert body["settings"]["allowOfflineSales"] is True


async def test_store_settings_update_merges_and_audits(
    client: AsyncClient, session: AsyncSession, tenant: dict[str, Any], auth_headers: Any
) -> None:
    response = await client.patch(
        f"/stores/{tenant['store'].id}/settings",
        json={"receiptFooter": "Get well soon"},
        headers=auth_headers(tenant),
    )
    assert response.status_code == 200
    settings = response.json()["data"]["settings"]
    assert settings["receiptFooter"] == "Get well soon"
    assert settings["lowStockAlerts"] is True

    store = await _refetch(session, tenant["store"].id)
    assert store.settings["receipt_footer"] == "Get well soon"
    assert "store.settings_updated" in await _audit_actions(session, store.id)


async def test_receipt_settings_validate_layout_and_redact_uploaded_logo(
    client: AsyncClient, session: AsyncSession, tenant: dict[str, Any], auth_headers: Any
) -> None:
    logo = "data:image/png;base64,iVBORw0KGgo="
    response = await client.patch(
        f"/stores/{tenant['store'].id}/settings",
        json={
            "receiptLogo": logo,
            "receiptPaperWidthMm": 58,
            "receiptBusinessName": "Care Pharmacy",
            "receiptShowTotal": False,
        },
        headers=auth_headers(tenant),
    )
    assert response.status_code == 200
    settings = response.json()["data"]["settings"]
    assert settings["receiptLogo"] == logo
    assert settings["receiptPaperWidthMm"] == 58
    assert settings["receiptShowTotal"] is False

    audit = await session.scalar(
        select(AuditLog)
        .where(AuditLog.store_id == tenant["store"].id)
        .order_by(AuditLog.created_at.desc())
    )
    assert audit is not None
    assert audit.after_data is not None
    assert audit.after_data["receipt_logo"] == "[redacted]"


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ({"receiptPaperWidthMm": 47}, "greater than or equal to 48"),
        ({"receiptPaperWidthMm": 211}, "less than or equal to 210"),
        ({"receiptLogo": "http://example.com/logo.png"}, "HTTPS URL"),
        ({"receiptLogo": "data:image/svg+xml;base64,PHN2Zz4="}, "PNG, JPEG, or WebP"),
    ],
)
async def test_receipt_settings_reject_invalid_print_configuration(
    client: AsyncClient, tenant: dict[str, Any], auth_headers: Any, payload: dict[str, Any], message: str
) -> None:
    response = await client.patch(
        f"/stores/{tenant['store'].id}/settings", json=payload, headers=auth_headers(tenant)
    )
    assert response.status_code == 422
    assert message in str(response.json())


async def test_store_settings_update_rejects_unknown_keys(
    client: AsyncClient, tenant: dict[str, Any], auth_headers: Any
) -> None:
    response = await client.patch(
        f"/stores/{tenant['store'].id}/settings",
        json={"turboMode": True},
        headers=auth_headers(tenant),
    )
    assert response.status_code == 422


async def test_cashier_cannot_change_store_settings(
    client: AsyncClient,
    session: AsyncSession,
    tenant: dict[str, Any],
    make_user: Callable[..., Any],
    make_membership: Callable[..., Any],
) -> None:
    headers = await _staff(
        session,
        tenant,
        make_user,
        make_membership,
        role=Role.CASHIER,
        phone="+8801700000025",
        store=tenant["store"],
    )
    assert (
        await client.get(f"/stores/{tenant['store'].id}/settings", headers=headers)
    ).status_code == 200
    for path, payload in (
        (f"/stores/{tenant['store'].id}", {"name": "Cashier Rename"}),
        (f"/stores/{tenant['store'].id}/settings", {"lowStockAlerts": False}),
        (f"/stores/{tenant['store'].id}/operating-status", {"status": "inactive"}),
    ):
        response = await client.patch(path, json=payload, headers=headers)
        assert response.status_code == 403
        assert response.json()["code"] == "FORBIDDEN"


async def test_manager_may_administer_the_branch(
    client: AsyncClient,
    session: AsyncSession,
    tenant: dict[str, Any],
    make_user: Callable[..., Any],
    make_membership: Callable[..., Any],
) -> None:
    headers = await _staff(
        session,
        tenant,
        make_user,
        make_membership,
        role=Role.MANAGER,
        phone="+8801700000026",
        store=tenant["store"],
    )
    response = await client.patch(
        f"/stores/{tenant['store'].id}/settings",
        json={"lowStockAlerts": False},
        headers=headers,
    )
    assert response.status_code == 200
    assert response.json()["data"]["settings"]["lowStockAlerts"] is False


# --- operating status -------------------------------------------------------


async def test_operating_status_change_is_persisted_and_audited(
    client: AsyncClient, session: AsyncSession, tenant: dict[str, Any], auth_headers: Any
) -> None:
    response = await client.patch(
        f"/stores/{tenant['store'].id}/operating-status",
        json={"status": "inactive", "reason": "Renovation"},
        headers=auth_headers(tenant),
    )
    assert response.status_code == 200
    body = response.json()["data"]
    assert body["status"] == RecordStatus.INACTIVE.value
    assert body["operational"] is False

    store = await _refetch(session, tenant["store"].id)
    assert store.status is RecordStatus.INACTIVE
    assert "store.status_changed" in await _audit_actions(session, store.id)


async def test_inactive_store_is_readable_but_not_operational(
    client: AsyncClient, session: AsyncSession, tenant: dict[str, Any], auth_headers: Any
) -> None:
    """The client must be able to see *why* trading is blocked, so reads stay open."""
    await client.patch(
        f"/stores/{tenant['store'].id}/operating-status",
        json={"status": "inactive"},
        headers=auth_headers(tenant),
    )
    response = await client.get("/stores/current", headers=auth_headers(tenant))
    assert response.status_code == 200
    assert response.json()["data"]["status"] == RecordStatus.INACTIVE.value

    store = await _refetch(session, tenant["store"].id)
    with pytest.raises(Forbidden):
        require_operational_store(store)


async def test_suspended_store_configuration_is_locked(
    client: AsyncClient, tenant: dict[str, Any], auth_headers: Any
) -> None:
    await client.patch(
        f"/stores/{tenant['store'].id}/operating-status",
        json={"status": "suspended", "reason": "Billing hold"},
        headers=auth_headers(tenant),
    )
    blocked = await client.patch(
        f"/stores/{tenant['store'].id}/settings",
        json={"lowStockAlerts": False},
        headers=auth_headers(tenant),
    )
    assert blocked.status_code == 403
    assert blocked.json()["code"] == "FORBIDDEN"

    reactivated = await client.patch(
        f"/stores/{tenant['store'].id}/operating-status",
        json={"status": "active"},
        headers=auth_headers(tenant),
    )
    assert reactivated.status_code == 200
    assert reactivated.json()["data"]["operational"] is True


async def test_operating_status_rejects_unknown_status(
    client: AsyncClient, tenant: dict[str, Any], auth_headers: Any
) -> None:
    response = await client.patch(
        f"/stores/{tenant['store'].id}/operating-status",
        json={"status": "closed-forever"},
        headers=auth_headers(tenant),
    )
    assert response.status_code == 422


async def test_reactivated_store_is_operational(
    session: AsyncSession, tenant: dict[str, Any]
) -> None:
    store = await _refetch(session, tenant["store"].id)
    assert require_operational_store(store) is store

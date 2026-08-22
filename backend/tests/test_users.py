from __future__ import annotations

import json
from collections.abc import Callable
from datetime import timedelta
from typing import Any
from uuid import UUID, uuid4

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuditLog, OrganizationUser, Role, StoreUser
from app.models import Session as SessionModel
from app.security import generate_token, hash_token, sign_access_token, utc_now

# Every (method, path, payload) triple the router exposes, so the cross-tenant and
# role-gate sweeps stay honest about covering the whole surface.
ROUTE_TABLE: tuple[tuple[str, str, dict[str, Any] | None], ...] = (
    ("get", "/users", None),
    ("post", "/users", {"phone": "017130000000", "displayName": "Sweep Staff", "role": "cashier"}),
    ("get", "/users/{user_id}", None),
    ("patch", "/users/{user_id}", {"displayName": "Renamed Staff"}),
    ("patch", "/users/{user_id}/role", {"role": "cashier"}),
    ("patch", "/users/{user_id}/status", {"status": "inactive"}),
    ("delete", "/users/{user_id}/membership", None),
    ("put", "/users/{user_id}/pin", {"pin": "4321"}),
    ("delete", "/users/{user_id}/pin", None),
    ("post", "/users/{user_id}/stores", {"storeId": "{store_id}"}),
    ("delete", "/users/{user_id}/stores/{store_id}", None),
)


def _fill(path: str, user_id: UUID, store_id: UUID) -> str:
    return path.format(user_id=str(user_id), store_id=str(store_id))


def _fill_payload(payload: dict[str, Any] | None, store_id: UUID) -> dict[str, Any] | None:
    if payload is None:
        return None
    return {key: _fill(str(value), UUID(int=0), store_id) for key, value in payload.items()}


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
    membership = await make_membership(organization, owner, Role.OWNER, store)
    await session.commit()
    return {"organization": organization, "owner": owner, "store": store, "membership": membership}


async def _staff(
    session: AsyncSession,
    tenant: dict[str, Any],
    make_user: Callable[..., Any],
    make_membership: Callable[..., Any],
    *,
    role: Role,
    phone: str,
    store: Any = None,
    pin: str | None = None,
) -> dict[str, Any]:
    """A committed staff member with their own live session, so their token is real."""
    user = await make_user(phone=phone, display_name=role.value, pin=pin)
    await make_membership(tenant["organization"], user, role, store)
    auth_session = await _auth_session(session, user, tenant["organization"], store)
    await session.commit()
    return {"user": user, "session": auth_session, "headers": _bearer(
        auth_session, user, tenant["organization"], store=store, role=role
    )}


async def _promote(
    client: AsyncClient, session: AsyncSession, tenant: dict[str, Any], auth_headers: Any, staff: dict[str, Any]
) -> None:
    """Grant a second owner so last-owner protection no longer bites."""
    response = await client.patch(
        f"/users/{staff['user'].id}/role",
        json={"role": "owner"},
        headers=auth_headers(tenant),
    )
    assert response.status_code == 200
    await session.commit()


# --- last-owner protection --------------------------------------------------


async def test_last_owner_cannot_be_demoted(
    client: AsyncClient, tenant: dict[str, Any], auth_headers: Any
) -> None:
    response = await client.patch(
        f"/users/{tenant['owner'].id}/role",
        json={"role": "manager"},
        headers=auth_headers(tenant),
    )
    assert response.status_code == 409
    assert response.json()["code"] == "CONFLICT"
    assert "owner" in response.json()["message"]


async def test_last_owner_cannot_be_deactivated(
    client: AsyncClient, tenant: dict[str, Any], auth_headers: Any
) -> None:
    response = await client.patch(
        f"/users/{tenant['owner'].id}/status",
        json={"status": "inactive"},
        headers=auth_headers(tenant),
    )
    assert response.status_code == 409


async def test_last_owner_membership_cannot_be_removed(
    client: AsyncClient, tenant: dict[str, Any], auth_headers: Any
) -> None:
    response = await client.delete(
        f"/users/{tenant['owner'].id}/membership", headers=auth_headers(tenant)
    )
    assert response.status_code == 409


async def test_owner_operations_succeed_once_a_second_owner_exists(
    client: AsyncClient,
    session: AsyncSession,
    tenant: dict[str, Any],
    auth_headers: Any,
    make_user: Callable[..., Any],
    make_membership: Callable[..., Any],
) -> None:
    successor = await _staff(
        session,
        tenant,
        make_user,
        make_membership,
        role=Role.MANAGER,
        phone="+8801700000031",
        store=tenant["store"],
    )
    await _promote(client, session, tenant, auth_headers, successor)

    demoted = await client.patch(
        f"/users/{tenant['owner'].id}/role",
        json={"role": "manager"},
        headers=auth_headers(tenant),
    )
    assert demoted.status_code == 200
    assert demoted.json()["data"]["membership"]["role"] == "manager"

    removed = await client.delete(
        f"/users/{tenant['owner'].id}/membership", headers=auth_headers(tenant)
    )
    assert removed.status_code == 200
    assert removed.json()["data"]["membership"]["status"] == "inactive"


# --- role grants -------------------------------------------------------------


async def test_manager_cannot_grant_the_owner_role(
    client: AsyncClient,
    session: AsyncSession,
    tenant: dict[str, Any],
    make_user: Callable[..., Any],
    make_membership: Callable[..., Any],
) -> None:
    manager = await _staff(
        session,
        tenant,
        make_user,
        make_membership,
        role=Role.MANAGER,
        phone="+8801700000032",
        store=tenant["store"],
    )
    cashier = await _staff(
        session,
        tenant,
        make_user,
        make_membership,
        role=Role.CASHIER,
        phone="+8801700000033",
        store=tenant["store"],
    )
    response = await client.patch(
        f"/users/{cashier['user'].id}/role", json={"role": "owner"}, headers=manager["headers"]
    )
    assert response.status_code == 403
    assert response.json()["code"] == "FORBIDDEN"

    membership = await session.scalar(
        select(OrganizationUser).where(OrganizationUser.user_id == cashier["user"].id)
    )
    assert membership is not None and membership.role is Role.CASHIER


async def test_owner_role_cannot_be_assigned_at_create(
    client: AsyncClient, tenant: dict[str, Any], auth_headers: Any
) -> None:
    response = await client.post(
        "/users",
        json={"phone": "017130000001", "displayName": "Minted Owner", "role": "owner"},
        headers=auth_headers(tenant),
    )
    assert response.status_code == 422
    assert response.json()["code"] == "VALIDATION_ERROR"


async def test_manager_cannot_administer_an_owner(
    client: AsyncClient,
    session: AsyncSession,
    tenant: dict[str, Any],
    make_user: Callable[..., Any],
    make_membership: Callable[..., Any],
) -> None:
    manager = await _staff(
        session,
        tenant,
        make_user,
        make_membership,
        role=Role.MANAGER,
        phone="+8801700000034",
        store=tenant["store"],
    )
    response = await client.patch(
        f"/users/{tenant['owner'].id}",
        json={"displayName": "Downgraded Owner"},
        headers=manager["headers"],
    )
    assert response.status_code == 403


# --- session revocation ------------------------------------------------------


async def test_deactivating_a_member_revokes_their_live_sessions(
    client: AsyncClient,
    session: AsyncSession,
    tenant: dict[str, Any],
    auth_headers: Any,
    make_user: Callable[..., Any],
    make_membership: Callable[..., Any],
) -> None:
    staff = await _staff(
        session,
        tenant,
        make_user,
        make_membership,
        role=Role.CASHIER,
        phone="+8801700000035",
        store=tenant["store"],
    )
    assert (await client.get("/users", headers=auth_headers(tenant))).status_code == 200
    before = await client.get("/stores/current", headers=staff["headers"])
    assert before.status_code == 200

    deactivated = await client.patch(
        f"/users/{staff['user'].id}/status",
        json={"status": "inactive", "reason": "Left the branch"},
        headers=auth_headers(tenant),
    )
    assert deactivated.status_code == 200

    # The API commits on its own session; bypass this session's identity map.
    revoked = await session.get(SessionModel, staff["session"].id, populate_existing=True)
    assert revoked is not None and revoked.revoked_at is not None
    after = await client.get("/stores/current", headers=staff["headers"])
    assert after.status_code == 401
    assert after.json()["code"] == "UNAUTHORIZED"


async def test_removing_membership_revokes_their_live_sessions(
    client: AsyncClient,
    session: AsyncSession,
    tenant: dict[str, Any],
    auth_headers: Any,
    make_user: Callable[..., Any],
    make_membership: Callable[..., Any],
) -> None:
    staff = await _staff(
        session,
        tenant,
        make_user,
        make_membership,
        role=Role.INVENTORY_STAFF,
        phone="+8801700000036",
        store=tenant["store"],
    )
    response = await client.delete(
        f"/users/{staff['user'].id}/membership", headers=auth_headers(tenant)
    )
    assert response.status_code == 200

    row = await session.get(SessionModel, staff["session"].id, populate_existing=True)
    assert row is not None and row.revoked_at is not None
    probe = await client.get("/stores/current", headers=staff["headers"])
    assert probe.status_code == 401


# --- store scope -------------------------------------------------------------


async def test_a_foreign_tenants_store_cannot_be_assigned(
    client: AsyncClient,
    session: AsyncSession,
    tenant: dict[str, Any],
    auth_headers: Any,
    make_user: Callable[..., Any],
    make_membership: Callable[..., Any],
    make_organization: Callable[..., Any],
    make_store: Callable[..., Any],
) -> None:
    other = await _second_tenant(
        session, make_organization, make_user, make_store, make_membership
    )
    staff = await _staff(
        session,
        tenant,
        make_user,
        make_membership,
        role=Role.CASHIER,
        phone="+8801700000037",
        store=tenant["store"],
    )
    response = await client.post(
        f"/users/{staff['user'].id}/stores",
        json={"storeId": str(other["store"].id)},
        headers=auth_headers(tenant),
    )
    assert response.status_code in (403, 404)
    assert "Rival" not in response.text

    rows = list(
        await session.scalars(select(StoreUser).where(StoreUser.user_id == staff["user"].id))
    )
    assert [row.store_id for row in rows] == [tenant["store"].id]


async def test_removing_membership_withdraws_branch_access(
    client: AsyncClient,
    session: AsyncSession,
    tenant: dict[str, Any],
    auth_headers: Any,
    make_user: Callable[..., Any],
    make_membership: Callable[..., Any],
) -> None:
    staff = await _staff(
        session,
        tenant,
        make_user,
        make_membership,
        role=Role.CASHIER,
        phone="+8801700000038",
        store=tenant["store"],
    )
    response = await client.delete(
        f"/users/{staff['user'].id}/membership", headers=auth_headers(tenant)
    )
    assert response.status_code == 200

    assignment = await session.scalar(
        select(StoreUser).where(
            StoreUser.store_id == tenant["store"].id, StoreUser.user_id == staff["user"].id
        )
    )
    assert assignment is not None and assignment.active is False


async def test_role_change_keeps_branch_rows_in_step(
    client: AsyncClient,
    session: AsyncSession,
    tenant: dict[str, Any],
    auth_headers: Any,
    make_user: Callable[..., Any],
    make_membership: Callable[..., Any],
) -> None:
    staff = await _staff(
        session,
        tenant,
        make_user,
        make_membership,
        role=Role.CASHIER,
        phone="+8801700000039",
        store=tenant["store"],
    )
    response = await client.patch(
        f"/users/{staff['user'].id}/role",
        json={"role": "manager"},
        headers=auth_headers(tenant),
    )
    assert response.status_code == 200
    assert response.json()["data"]["storeMemberships"][0]["role"] == "manager"

    assignment = await session.scalar(
        select(StoreUser).where(StoreUser.user_id == staff["user"].id)
    )
    assert assignment is not None and assignment.role is Role.MANAGER


async def test_store_assignment_lifecycle(
    client: AsyncClient,
    session: AsyncSession,
    tenant: dict[str, Any],
    auth_headers: Any,
    make_user: Callable[..., Any],
    make_membership: Callable[..., Any],
) -> None:
    staff = await _staff(
        session,
        tenant,
        make_user,
        make_membership,
        role=Role.CASHIER,
        phone="+8801700000040",
    )
    assigned = await client.post(
        f"/users/{staff['user'].id}/stores",
        json={"storeId": str(tenant["store"].id)},
        headers=auth_headers(tenant),
    )
    assert assigned.status_code == 201
    assert assigned.json()["data"]["storeMemberships"][0]["status"] == "active"

    duplicate = await client.post(
        f"/users/{staff['user'].id}/stores",
        json={"storeId": str(tenant["store"].id)},
        headers=auth_headers(tenant),
    )
    assert duplicate.status_code == 409

    removed = await client.delete(
        f"/users/{staff['user'].id}/stores/{tenant['store'].id}", headers=auth_headers(tenant)
    )
    assert removed.status_code == 200
    assert removed.json()["data"]["storeMemberships"][0]["status"] == "inactive"

    missing = await client.delete(
        f"/users/{staff['user'].id}/stores/{tenant['store'].id}", headers=auth_headers(tenant)
    )
    assert missing.status_code == 404


# --- phone uniqueness and normalization --------------------------------------


async def test_duplicate_phone_conflicts_without_leaking_the_other_tenant(
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
    response = await client.post(
        "/users",
        json={"phone": other["owner"].phone, "displayName": "Same Number", "role": "cashier"},
        headers=auth_headers(tenant),
    )
    assert response.status_code == 409
    assert "Rival" not in response.text


async def test_phone_variants_resolve_to_one_account(
    client: AsyncClient, tenant: dict[str, Any], auth_headers: Any
) -> None:
    created = await client.post(
        "/users",
        json={"phone": "01712 345678", "displayName": "Local Format", "role": "cashier"},
        headers=auth_headers(tenant),
    )
    assert created.status_code == 201
    assert created.json()["data"]["phone"] == "+8801712345678"

    clash = await client.post(
        "/users",
        json={"phone": "+8801712345678", "displayName": "International Format", "role": "cashier"},
        headers=auth_headers(tenant),
    )
    assert clash.status_code == 409

    renamed = await client.patch(
        f"/users/{tenant['owner'].id}",
        json={"phone": "01712-345678"},
        headers=auth_headers(tenant),
    )
    assert renamed.status_code == 409


# --- PIN lifecycle -----------------------------------------------------------


async def test_pin_lifecycle_flips_pin_set(
    client: AsyncClient,
    session: AsyncSession,
    tenant: dict[str, Any],
    auth_headers: Any,
    make_user: Callable[..., Any],
    make_membership: Callable[..., Any],
) -> None:
    staff = await _staff(
        session, tenant, make_user, make_membership, role=Role.CASHIER, phone="+8801700000041"
    )
    assert staff["user"].pin_hash is None

    weak = await client.put(
        f"/users/{staff['user'].id}/pin", json={"pin": "1111"}, headers=auth_headers(tenant)
    )
    assert weak.status_code == 422

    set_response = await client.put(
        f"/users/{staff['user'].id}/pin", json={"pin": "4321"}, headers=auth_headers(tenant)
    )
    assert set_response.status_code == 200
    assert set_response.json()["data"] == {"userId": str(staff["user"].id), "pinSet": True}

    cleared = await client.delete(
        f"/users/{staff['user'].id}/pin", headers=auth_headers(tenant)
    )
    assert cleared.status_code == 200
    assert cleared.json()["data"]["pinSet"] is False


async def test_no_users_response_ever_contains_the_pin_or_its_hash(
    client: AsyncClient,
    session: AsyncSession,
    tenant: dict[str, Any],
    auth_headers: Any,
    make_user: Callable[..., Any],
    make_membership: Callable[..., Any],
) -> None:
    def keys(value: Any) -> set[str]:
        if isinstance(value, dict):
            found: set[str] = set()
            for key, item in value.items():
                found.add(str(key))
                found |= keys(item)
            return found
        if isinstance(value, list):
            merged: set[str] = set()
            for item in value:
                merged |= keys(item)
            return merged
        return set()

    staff = await _staff(
        session,
        tenant,
        make_user,
        make_membership,
        role=Role.CASHIER,
        phone="+8801700000042",
        store=tenant["store"],
        pin="4321",
    )
    user_id = staff["user"].id
    store_id = tenant["store"].id
    for method, path, payload in ROUTE_TABLE:
        response = await client.request(
            method,
            _fill(path, user_id, store_id),
            json=_fill_payload(payload, store_id),
            headers=auth_headers(tenant),
        )
        # ``pinSet`` is fine; a field that actually carries the secret is not. The
        # argon2 check catches a hash smuggled under any other name.
        assert not (keys(response.json()) & {"pin", "pinHash", "pin_hash"}), (method, path)
        assert "argon2" not in response.text


# --- authorization on every route --------------------------------------------


async def test_staff_roles_are_denied_on_every_users_route(
    client: AsyncClient,
    session: AsyncSession,
    tenant: dict[str, Any],
    make_user: Callable[..., Any],
    make_membership: Callable[..., Any],
) -> None:
    """The roster carries colleagues' phone numbers, so reads are gated as well."""
    user_id = tenant["owner"].id
    store_id = tenant["store"].id
    for role, phone in (
        (Role.CASHIER, "+8801700000043"),
        (Role.INVENTORY_STAFF, "+8801700000044"),
    ):
        staff = await _staff(
            session, tenant, make_user, make_membership, role=role, phone=phone, store=tenant["store"]
        )
        for method, path, payload in ROUTE_TABLE:
            response = await client.request(
                method,
                _fill(path, user_id, store_id),
                json=_fill_payload(payload, store_id),
                headers=staff["headers"],
            )
            assert response.status_code == 403, (role, method, path)
            assert response.json()["code"] == "FORBIDDEN"


async def test_unauthenticated_access_is_denied(
    client: AsyncClient, tenant: dict[str, Any]
) -> None:
    user_id = tenant["owner"].id
    store_id = tenant["store"].id
    for method, path, payload in ROUTE_TABLE:
        response = await client.request(
            method, _fill(path, user_id, store_id), json=_fill_payload(payload, store_id)
        )
        assert response.status_code == 401, (method, path)


# --- cross-tenant isolation --------------------------------------------------


async def test_direct_object_access_to_another_tenants_user_is_denied(
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
    foreign_user = other["owner"].id
    foreign_store = other["store"].id
    headers = auth_headers(tenant)

    for method, path, payload in ROUTE_TABLE:
        if path == "/users":
            continue  # the list route is tenant-scoped, not object-scoped
        response = await client.request(
            method,
            _fill(path, foreign_user, foreign_store),
            json=_fill_payload(payload, foreign_store),
            headers=headers,
        )
        assert response.status_code in (403, 404), (method, path)
        assert "Rival" not in response.text

    membership = await session.scalar(
        select(OrganizationUser).where(OrganizationUser.user_id == foreign_user)
    )
    assert membership is not None and membership.active is True


async def test_unknown_user_id_is_not_found(
    client: AsyncClient, tenant: dict[str, Any], auth_headers: Any
) -> None:
    response = await client.get(f"/users/{uuid4()}", headers=auth_headers(tenant))
    assert response.status_code == 404
    assert response.json()["code"] == "NOT_FOUND"


# --- roster ------------------------------------------------------------------


async def test_roster_lists_staff_with_membership_and_pin_flag(
    client: AsyncClient,
    session: AsyncSession,
    tenant: dict[str, Any],
    auth_headers: Any,
    make_user: Callable[..., Any],
    make_membership: Callable[..., Any],
) -> None:
    staff = await _staff(
        session,
        tenant,
        make_user,
        make_membership,
        role=Role.CASHIER,
        phone="+8801700000045",
        store=tenant["store"],
        pin="4321",
    )
    response = await client.get("/users", headers=auth_headers(tenant))
    assert response.status_code == 200
    page = response.json()["data"]
    assert page["total"] == 2
    entry = next(
        item for item in page["items"] if item["id"] == str(staff["user"].id)
    )
    assert entry["membership"]["status"] == "active"
    assert entry["storeMemberships"][0]["storeId"] == str(tenant["store"].id)
    assert entry["pinSet"] is True
    assert entry["phone"] == staff["user"].phone


# --- audit coverage ----------------------------------------------------------


async def test_mutations_write_redacted_audit_rows(
    client: AsyncClient,
    session: AsyncSession,
    tenant: dict[str, Any],
    auth_headers: Any,
    make_user: Callable[..., Any],
    make_membership: Callable[..., Any],
) -> None:
    staff = await _staff(
        session, tenant, make_user, make_membership, role=Role.CASHIER, phone="+8801700000046"
    )
    user_id = staff["user"].id
    store_id = tenant["store"].id
    await client.post(
        "/users",
        json={"phone": "017130000002", "displayName": "Audited Staff", "role": "cashier"},
        headers=auth_headers(tenant),
    )
    await client.patch(f"/users/{user_id}", json={"displayName": "Renamed"}, headers=auth_headers(tenant))
    await client.patch(f"/users/{user_id}/role", json={"role": "manager"}, headers=auth_headers(tenant))
    await client.patch(
        f"/users/{user_id}/status", json={"status": "inactive"}, headers=auth_headers(tenant)
    )
    await client.patch(f"/users/{user_id}/status", json={"status": "active"}, headers=auth_headers(tenant))
    await client.put(f"/users/{user_id}/pin", json={"pin": "4321"}, headers=auth_headers(tenant))
    await client.delete(f"/users/{user_id}/pin", headers=auth_headers(tenant))
    await client.put(f"/users/{user_id}/pin", json={"pin": "4321"}, headers=auth_headers(tenant))
    await client.post(
        f"/users/{user_id}/stores", json={"storeId": str(store_id)}, headers=auth_headers(tenant)
    )
    await client.delete(
        f"/users/{user_id}/stores/{store_id}", headers=auth_headers(tenant)
    )
    await client.delete(f"/users/{user_id}/membership", headers=auth_headers(tenant))

    rows = list(
        await session.scalars(
            select(AuditLog).where(
                AuditLog.organization_id == tenant["organization"].id,
                AuditLog.entity_type == "user",
            )
        )
    )
    actions = {row.action for row in rows}
    assert actions == {
        "user.created",
        "user.updated",
        "user.role_changed",
        "user.status_changed",
        "user.pin_reset",
        "user.pin_cleared",
        "user.store_assigned",
        "user.store_unassigned",
        "user.membership_removed",
    }
    for row in rows:
        blob = json.dumps({"before": row.before_data, "after": row.after_data})
        assert "4321" not in blob
    pin_reset = next(row for row in rows if row.action == "user.pin_reset")
    assert pin_reset.after_data is not None and pin_reset.after_data["pinSet"] is True

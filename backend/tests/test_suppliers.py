from __future__ import annotations

import uuid
from collections.abc import Callable
from decimal import Decimal
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.products import PharmacyProduct
from app.domains.suppliers import SupplierProduct
from app.main import app
from app.models import OrganizationUser, Role, SupplierLedgerEntry, User
from app.services.suppliers import append_ledger_entry, supplier_balance


async def _make_product(session: AsyncSession, organization: Any, name: str = "Napa Extra") -> PharmacyProduct:
    product = PharmacyProduct(
        organization_id=organization.id, name=name, unit="strip", active=True
    )
    session.add(product)
    await session.flush()
    return product


def _key(tag: str) -> str:
    return f"idem-{tag}-0123456789abcdef"[:32].ljust(24, "0")


@pytest.fixture
async def supplier(client: AsyncClient, auth_headers: Callable[..., dict[str, str]], tenant: dict[str, Any]) -> dict[str, Any]:
    response = await client.post(
        "/suppliers",
        json={"name": "Acme Pharma Distributors", "phone": "+8801555000000"},
        headers=auth_headers(tenant),
    )
    assert response.status_code == 201, response.text
    return response.json()["data"]


# --- CRUD -------------------------------------------------------------------


async def test_create_supplier_and_duplicate_name_conflict(
    client: AsyncClient, auth_headers: Callable[..., dict[str, str]], tenant: dict[str, Any]
) -> None:
    headers = auth_headers(tenant)
    first = await client.post("/suppliers", json={"name": "MediSource Ltd"}, headers=headers)
    assert first.status_code == 201
    body = first.json()["data"]
    assert body["name"] == "MediSource Ltd"
    assert body["status"] == "active"

    duplicate = await client.post("/suppliers", json={"name": "MediSource Ltd"}, headers=headers)
    assert duplicate.status_code == 409


async def test_update_contact_info(
    client: AsyncClient,
    auth_headers: Callable[..., dict[str, str]],
    tenant: dict[str, Any],
    supplier: dict[str, Any],
) -> None:
    response = await client.patch(
        f"/suppliers/{supplier['id']}",
        json={"phone": "+8801999111222", "address": "12 Motijheel, Dhaka"},
        headers=auth_headers(tenant),
    )
    assert response.status_code == 200
    body = response.json()["data"]
    assert body["phone"] == "+8801999111222"
    assert body["address"] == "12 Motijheel, Dhaka"


async def test_cross_tenant_supplier_is_404(
    client: AsyncClient,
    session: AsyncSession,
    auth_headers: Callable[..., dict[str, str]],
    tenant: dict[str, Any],
    make_organization: Callable[..., Any],
    make_user: Callable[..., Any],
    make_membership: Callable[..., Any],
) -> None:
    other_org = await make_organization(name="Rival", slug="rival-org")
    rival_owner = await make_user(phone="+8801777777777")
    await make_membership(other_org, rival_owner, Role.OWNER)
    from datetime import timedelta

    from app.models import Session as SessionModel
    from app.security import generate_token, hash_token, utc_now

    rival_session = SessionModel(
        user_id=rival_owner.id,
        organization_id=other_org.id,
        store_id=None,
        refresh_token_hash=hash_token(generate_token()),
        expires_at=utc_now() + timedelta(days=30),
    )
    session.add(rival_session)
    await session.commit()

    created = await client.post(
        "/suppliers",
        json={"name": "Secret Supplier"},
        headers=auth_headers(
            {
                "organization": other_org,
                "owner": rival_owner,
                "store": None,
                "session": rival_session,
            },
            with_store=False,
        ),
    )
    assert created.status_code == 201
    foreign_id = created.json()["data"]["id"]

    response = await client.get(f"/suppliers/{foreign_id}", headers=auth_headers(tenant))
    assert response.status_code == 404


async def test_deactivation_retains_ledger(
    client: AsyncClient,
    session: AsyncSession,
    auth_headers: Callable[..., dict[str, str]],
    tenant: dict[str, Any],
    supplier: dict[str, Any],
) -> None:
    context = _context(tenant)
    await append_ledger_entry(
        session,
        context,
        uuid.UUID(supplier["id"]),
        "purchase",
        Decimal("500.00"),
        idempotency_key=_key("purchase"),
    )

    deactivated = await client.patch(
        f"/suppliers/{supplier['id']}/status",
        json={"status": "inactive"},
        headers=auth_headers(tenant),
    )
    assert deactivated.status_code == 200
    assert deactivated.json()["data"]["status"] == "inactive"

    ledger = await client.get(f"/suppliers/{supplier['id']}/ledger", headers=auth_headers(tenant))
    assert ledger.status_code == 200
    assert ledger.json()["data"]["total"] == 1
    balance = await client.get(f"/suppliers/{supplier['id']}/balance", headers=auth_headers(tenant))
    assert balance.status_code == 200
    assert Decimal(balance.json()["data"]["balance"]) == Decimal("500.00")


# --- product mappings -------------------------------------------------------


async def test_mapping_preferred_exclusivity(
    client: AsyncClient,
    session: AsyncSession,
    auth_headers: Callable[..., dict[str, str]],
    tenant: dict[str, Any],
    supplier: dict[str, Any],
) -> None:
    product = await _make_product(session, tenant["organization"])
    headers = auth_headers(tenant)

    second = await client.post(
        "/suppliers", json={"name": "Backup Supplier"}, headers=headers
    )
    second_id = second.json()["data"]["id"]

    first_link = await client.post(
        f"/suppliers/{supplier['id']}/products",
        json={"pharmacy_product_id": str(product.id), "supplier_sku": "ACME-1", "preferred": True},
        headers=headers,
    )
    assert first_link.status_code == 201, first_link.text
    assert first_link.json()["data"]["preferred"] is True

    second_link = await client.post(
        f"/suppliers/{second_id}/products",
        json={"pharmacy_product_id": str(product.id), "preferred": True},
        headers=headers,
    )
    assert second_link.status_code == 201

    preferred_count = await session.scalar(
        select(func.count())
        .select_from(SupplierLedgerEntry.metadata.tables["supplier_products"])
        .where(SupplierLedgerEntry.metadata.tables["supplier_products"].c.preferred.is_(True))
    )
    assert preferred_count == 1


# --- ledger -----------------------------------------------------------------


def _context(tenant: dict[str, Any]):
    from app.context import RequestContext

    return RequestContext(
        organization_id=tenant["organization"].id,
        user_id=tenant["owner"].id,
        role=Role.OWNER,
        store_id=tenant["store"].id,
        session_id=tenant["session"].id,
    )


async def test_payment_and_adjustment_balance_math(
    client: AsyncClient,
    session: AsyncSession,
    auth_headers: Callable[..., dict[str, str]],
    tenant: dict[str, Any],
    supplier: dict[str, Any],
) -> None:
    context = _context(tenant)
    headers = auth_headers(tenant)

    await append_ledger_entry(
        session, context, uuid.UUID(supplier["id"]), "purchase", Decimal("1000.00"),
        idempotency_key=_key("p1"),
    )
    payment = await client.post(
        f"/suppliers/{supplier['id']}/payments",
        json={"amount": "400.00", "note": "bank transfer"},
        headers={**headers, "Idempotency-Key": _key("pay1")},
    )
    assert payment.status_code == 200, payment.text
    assert Decimal(payment.json()["data"]["amount"]) == Decimal("-400.00")
    assert payment.json()["data"]["entryType"] == "payment"

    adjustment = await client.post(
        f"/suppliers/{supplier['id']}/adjustments",
        json={"amount": "-50.00", "note": "damaged goods credit"},
        headers={**headers, "Idempotency-Key": _key("adj1")},
    )
    assert adjustment.status_code == 200, adjustment.text
    assert Decimal(adjustment.json()["data"]["amount"]) == Decimal("-50.00")

    balance = await client.get(f"/suppliers/{supplier['id']}/balance", headers=headers)
    assert Decimal(balance.json()["data"]["balance"]) == Decimal("550.00")

    ledger = await client.get(f"/suppliers/{supplier['id']}/ledger?limit=10", headers=headers)
    entries = ledger.json()["data"]["items"]
    dates = [entry["createdAt"] for entry in entries]
    assert dates == sorted(dates, reverse=True)


async def test_adjustment_forbidden_for_cashier_role(
    client: AsyncClient,
    session: AsyncSession,
    auth_headers: Callable[..., dict[str, str]],
    tenant: dict[str, Any],
    supplier: dict[str, Any],
) -> None:
    from datetime import timedelta

    from app.models import Session as SessionModel
    from app.security import generate_token, hash_token, utc_now
    from tests.conftest import access_token_for

    cashier = None
    for user in (
        await session.scalars(select(User).where(User.phone == "+8801766666666"))
    ):
        cashier = user
    if cashier is None:
        from app.security import hash_secret

        cashier = User(
            phone="+8801766666666",
            display_name="Cashier",
            pin_hash=hash_secret("1234"),
        )
        session.add(cashier)
        await session.flush()
    session.add(
        OrganizationUser(
            organization_id=tenant["organization"].id,
            user_id=cashier.id,
            role=Role.CASHIER,
            active=True,
        )
    )
    cashier_session = SessionModel(
        user_id=cashier.id,
        organization_id=tenant["organization"].id,
        store_id=tenant["store"].id,
        refresh_token_hash=hash_token(generate_token()),
        expires_at=utc_now() + timedelta(days=30),
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
    cashier_headers = {"Authorization": f"Bearer {token}"}

    response = await client.post(
        f"/suppliers/{supplier['id']}/adjustments",
        json={"amount": "10.00"},
        headers={**cashier_headers, "Idempotency-Key": _key("cashier")},
    )
    assert response.status_code == 403

    payment = await client.post(
        f"/suppliers/{supplier['id']}/payments",
        json={"amount": "10.00"},
        headers={**cashier_headers, "Idempotency-Key": _key("cashier-pay")},
    )
    assert payment.status_code == 403


async def test_append_ledger_entry_is_idempotent(
    session: AsyncSession, tenant: dict[str, Any], supplier: dict[str, Any]
) -> None:
    context = _context(tenant)
    supplier_id = uuid.UUID(supplier["id"])
    key = _key("idem")

    first = await append_ledger_entry(
        session, context, supplier_id, "purchase", Decimal("250.00"), idempotency_key=key
    )
    second = await append_ledger_entry(
        session, context, supplier_id, "purchase", Decimal("250.00"), idempotency_key=key
    )
    assert first.id == second.id

    count = await session.scalar(
        select(func.count())
        .select_from(SupplierLedgerEntry)
        .where(SupplierLedgerEntry.organization_id == tenant["organization"].id)
    )
    assert count == 1
    assert await supplier_balance(session, supplier_id, organization_id=tenant["organization"].id) == Decimal("250.00")

from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select

from app.domains.customers import Customer, CustomerAddress
from app.domains.payments import Payment, PaymentMethod, PaymentRefund, PaymentStatus
from app.domains.sales import Sale, SaleReturn, SaleStatus
from app.models import AuditLog, Organization, Role
from app.models import Session as SessionModel
from app.security import generate_token, hash_token, utc_now
from tests.conftest import access_token_for


async def _create_customer(client: Any, headers: dict[str, str], **overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {"name": "Karim Uddin"}
    body.update(overrides)
    response = await client.post("/customers", json=body, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()["data"]


async def _role_headers(
    session: Any, tenant: dict[str, Any], user: Any, role: Role
) -> dict[str, str]:
    """Mint an access token backed by a real auth session row for ``user``."""
    auth_session = SessionModel(
        user_id=user.id,
        organization_id=tenant["organization"].id,
        store_id=tenant["store"].id,
        refresh_token_hash=hash_token(generate_token()),
        expires_at=utc_now() + timedelta(days=30),
    )
    session.add(auth_session)
    await session.commit()
    token = access_token_for(
        session_id=auth_session.id,
        user_id=user.id,
        organization_id=tenant["organization"].id,
        role=role,
        store_id=tenant["store"].id,
    )
    return {"Authorization": f"Bearer {token}"}


async def _rival_headers(
    session: Any, other_org: Organization, user: Any, store: Any
) -> dict[str, str]:
    auth_session = SessionModel(
        user_id=user.id,
        organization_id=other_org.id,
        store_id=store.id,
        refresh_token_hash=hash_token(generate_token()),
        expires_at=utc_now() + timedelta(days=30),
    )
    session.add(auth_session)
    await session.commit()
    token = access_token_for(
        session_id=auth_session.id,
        user_id=user.id,
        organization_id=other_org.id,
        role=Role.OWNER,
        store_id=store.id,
    )
    return {"Authorization": f"Bearer {token}"}


# --- creation & normalization ---------------------------------------------------


async def test_create_customer_normalizes_phone(
    client: Any, session: Any, tenant: dict[str, Any], auth_headers: Callable[..., dict[str, str]]
) -> None:
    headers = auth_headers(tenant)
    data = await _create_customer(
        client, headers, normalizedPhone="+880 1700-000000", email="karim@example.com"
    )
    assert data["normalizedPhone"] == "+8801700000000"
    assert data["active"] is True
    assert data["dueBalance"] in ("0", "0.00")

    audits = list(await session.scalars(select(AuditLog)))
    assert any(a.action == "customer.created" and a.entity_type == "customer" for a in audits)


async def test_create_customer_without_phone_is_allowed(
    client: Any, tenant: dict[str, Any], auth_headers: Callable[..., dict[str, str]]
) -> None:
    data = await _create_customer(client, auth_headers(tenant))
    assert data["normalizedPhone"] is None


async def test_invalid_phone_is_rejected(
    client: Any, tenant: dict[str, Any], auth_headers: Callable[..., dict[str, str]]
) -> None:
    response = await client.post(
        "/customers", json={"name": "Bad Phone", "normalizedPhone": "12345"}, headers=auth_headers(tenant)
    )
    assert response.status_code == 422


# --- duplicate detection across variants -----------------------------------------


async def test_phone_variants_dedupe_to_conflict(
    client: Any, session: Any, tenant: dict[str, Any], auth_headers: Callable[..., dict[str, str]]
) -> None:
    headers = auth_headers(tenant)
    first = await _create_customer(client, headers, normalizedPhone="+8801700000000")
    assert first["normalizedPhone"] == "+8801700000000"

    for variant in ("01700000000", "+880 1700-000000", "8801700000000"):
        response = await client.post(
            "/customers", json={"name": "Someone Else", "normalizedPhone": variant}, headers=headers
        )
        assert response.status_code == 409, (variant, response.text)

    # Same phone in another organization is fine.
    other_org: Organization = Organization(name="Rival", slug="rival", settings={})
    session.add(other_org)
    await session.flush()
    twin = Customer(organization_id=other_org.id, name="Twin", normalized_phone="+8801700000000")
    session.add(twin)
    await session.flush()


async def test_duplicate_phone_conflict_via_api(
    client: Any, tenant: dict[str, Any], auth_headers: Callable[..., dict[str, str]]
) -> None:
    headers = auth_headers(tenant)
    await _create_customer(client, headers, normalizedPhone="01812345678")
    response = await client.post(
        "/customers", json={"name": "Dup", "normalizedPhone":"+8801812345678"}, headers=headers
    )
    assert response.status_code == 409


# --- search -----------------------------------------------------------------------


async def test_search_by_name_prefix_and_phone_substring(
    client: Any, tenant: dict[str, Any], auth_headers: Callable[..., dict[str, str]]
) -> None:
    headers = auth_headers(tenant)
    await _create_customer(client, headers, name="Rahim Miya", normalizedPhone="01711111111")
    await _create_customer(client, headers, name="Karim", normalizedPhone="01822222222")

    by_name = await client.get("/customers?q=Rah", headers=headers)
    assert by_name.status_code == 200
    page = by_name.json()["data"]
    assert page["total"] == 1
    assert page["items"][0]["name"] == "Rahim Miya"

    by_phone = await client.get("/customers?q=2222", headers=headers)
    assert by_phone.status_code == 200
    items = by_phone.json()["data"]["items"]
    assert len(items) == 1
    assert items[0]["normalizedPhone"] == "+8801822222222"


# --- cross-tenant isolation --------------------------------------------------------


async def test_cross_tenant_customer_is_not_found(
    client: Any,
    session: Any,
    tenant: dict[str, Any],
    auth_headers: Callable[..., dict[str, str]],
    make_organization: Callable[..., Any],
    make_user: Callable[..., Any],
    make_store: Callable[..., Any],
    make_membership: Callable[..., Any],
) -> None:
    data = await _create_customer(client, auth_headers(tenant), normalizedPhone="01933334444")

    other_org = await make_organization(name="Rival", slug="rival")
    rival = await make_user(phone="+8801799999999", display_name="Rival Owner")
    rival_store = await make_store(other_org, code="RIVAL")
    from app.models import StoreUser

    session.add(
        StoreUser(store_id=rival_store.id, user_id=rival.id, role=Role.OWNER, active=True)
    )
    await make_membership(other_org, rival, Role.OWNER)

    response = await client.get(
        f"/customers/{data['id']}",
        headers=await _rival_headers(session, other_org, rival, rival_store),
    )
    assert response.status_code == 404


# --- update / deactivate -------------------------------------------------------------


async def test_update_and_deactivate_flow(
    client: Any,
    session: Any,
    tenant: dict[str, Any],
    auth_headers: Callable[..., dict[str, str]],
) -> None:
    headers = auth_headers(tenant)
    data = await _create_customer(client, headers, normalizedPhone="01655556666")

    updated = await client.patch(
        f"/customers/{data['id']}", json={"name": "Karim Updated"}, headers=headers
    )
    assert updated.status_code == 200
    assert updated.json()["data"]["name"] == "Karim Updated"

    deactivated = await client.delete(f"/customers/{data['id']}", headers=headers)
    assert deactivated.status_code == 200
    assert deactivated.json()["data"]["active"] is False

    audits = list(await session.scalars(select(AuditLog)))
    actions = {a.action for a in audits}
    assert "customer.updated" in actions
    assert "customer.deactivated" in actions


async def test_deactivate_requires_owner_or_manager_role(
    client: Any, session: Any, tenant: dict[str, Any], auth_headers: Callable[..., dict[str, str]], make_user: Callable[..., Any], make_membership: Callable[..., Any]
) -> None:
    headers = auth_headers(tenant)
    data = await _create_customer(client, headers, normalizedPhone="01544445555")

    cashier = await make_user(phone="+8801777777777", display_name="Cashier")
    await make_membership(tenant["organization"], cashier, Role.CASHIER, tenant["store"])
    cashier_session = SessionModel(
        user_id=cashier.id,
        organization_id=tenant["organization"].id,
        store_id=tenant["store"].id,
        refresh_token_hash=hash_token(generate_token()),
        expires_at=utc_now() + timedelta(days=30),
    )
    session.add(cashier_session)
    await session.commit()
    cashier_token = access_token_for(
        session_id=cashier_session.id,
        user_id=cashier.id,
        organization_id=tenant["organization"].id,
        role=Role.CASHIER,
        store_id=tenant["store"].id,
    )

    denied = await client.delete(
        f"/customers/{data['id']}", headers={"Authorization": f"Bearer {cashier_token}"}
    )
    assert denied.status_code == 403


# --- history summary --------------------------------------------------------------


async def test_history_summary_counts_completed_sales_only(
    client: Any, session: Any, tenant: dict[str, Any], auth_headers: Callable[..., dict[str, str]]
) -> None:
    headers = auth_headers(tenant)
    data = await _create_customer(client, headers)
    customer_id = UUID(data["id"])

    sale_rows = [
        Sale(
            organization_id=tenant["organization"].id,
            store_id=tenant["store"].id,
            customer_id=customer_id,
            status=status_value,
            subtotal=subtotal,
            total=subtotal,
            idempotency_key=f"sale-{uuid4().hex}",
        )
        for status_value, subtotal in [
            (SaleStatus.COMPLETED, Decimal("120.50")),
            (SaleStatus.COMPLETED, Decimal("79.50")),
            (SaleStatus.VOIDED, Decimal("999.00")),
        ]
    ]
    guest_sale = Sale(
        organization_id=tenant["organization"].id,
        store_id=tenant["store"].id,
        customer_id=None,
        status=SaleStatus.COMPLETED,
        subtotal=Decimal("50.00"),
        total=Decimal("50.00"),
        idempotency_key=f"guest-{uuid4().hex}",
    )
    session.add_all(sale_rows)
    session.add(guest_sale)
    await session.commit()

    summary_response = await client.get(f"/customers/{customer_id}/history", headers=headers)
    assert summary_response.status_code == 200
    summary = summary_response.json()["data"]
    assert summary["saleCount"] == 2
    assert summary["totalSpent"] == "200.00"
    assert summary["totalDue"] == "0.00"

    # Guest sales are unaffected by customer history.
    guest_count = len(list(await session.scalars(select(Sale).where(Sale.customer_id.is_(None)))))
    assert guest_count == 1


# --- addresses ----------------------------------------------------------------------


async def test_addresses_crud(
    client: Any, tenant: dict[str, Any], auth_headers: Callable[..., dict[str, str]]
) -> None:
    headers = auth_headers(tenant)
    data = await _create_customer(client, headers, normalizedPhone="01477778888")

    created = await client.post(
        f"/customers/{data['id']}/addresses",
        json={"label": "Home", "addressLine": "12/A Dhanmondi", "city": "Dhaka", "postalCode": "1209"},
        headers=headers,
    )
    assert created.status_code == 201, created.text
    address = created.json()["data"]
    assert address["label"] == "Home"
    assert address["addressLine"] == "12/A Dhanmondi"

    listing = await client.get(f"/customers/{data['id']}/addresses", headers=headers)
    assert listing.status_code == 200
    items = listing.json()["data"]
    assert len(items) == 1
    assert items[0]["id"] == address["id"]

    missing = await client.get(f"/customers/{uuid4()}/addresses", headers=headers)
    assert missing.status_code == 404


async def test_addresses_ignore_another_tenants_rows(
    client: Any,
    session: Any,
    tenant: dict[str, Any],
    auth_headers: Callable[..., dict[str, str]],
    make_organization: Callable[..., Any],
) -> None:
    """The address FK is not organization-scoped, so the query must be.

    Nothing stops a row in another tenant from naming this customer id, and a
    listing that filters on ``customer_id`` alone would happily serve it.
    """
    headers = auth_headers(tenant)
    data = await _create_customer(client, headers, normalizedPhone="01400001111")
    other_org = await make_organization(name="Rival Addr", slug="rival-addr")
    session.add(
        CustomerAddress(
            organization_id=other_org.id,
            customer_id=UUID(data["id"]),
            label="Leaked",
            address_line="Somewhere else",
        )
    )
    await session.commit()

    listing = await client.get(f"/customers/{data['id']}/addresses", headers=headers)
    assert listing.status_code == 200
    assert [address["label"] for address in listing.json()["data"]] == []


# --- audit fidelity ------------------------------------------------------------------


async def test_update_audit_keeps_the_previous_value(
    client: Any, session: Any, tenant: dict[str, Any], auth_headers: Callable[..., dict[str, str]]
) -> None:
    """``before`` must be a snapshot, not a second read of the mutated row.

    An audit trail whose before and after agree cannot answer the only question
    it exists to answer: what did this used to be?
    """
    headers = auth_headers(tenant)
    data = await _create_customer(client, headers, name="Karim Uddin")

    response = await client.patch(
        f"/customers/{data['id']}", json={"name": "Karim Corrected"}, headers=headers
    )
    assert response.status_code == 200

    entry = await session.scalar(select(AuditLog).where(AuditLog.action == "customer.updated"))
    assert entry is not None
    assert entry.before_data["name"] == "Karim Uddin"
    assert entry.after_data["name"] == "Karim Corrected"


# --- history scoping, netting, and permissions ---------------------------------------


async def test_history_ignores_another_tenants_sales(
    client: Any,
    session: Any,
    tenant: dict[str, Any],
    auth_headers: Callable[..., dict[str, str]],
    make_organization: Callable[..., Any],
    make_store: Callable[..., Any],
) -> None:
    """A sale row owned by another tenant must not reach this customer's totals."""
    headers = auth_headers(tenant)
    data = await _create_customer(client, headers)
    customer_id = UUID(data["id"])

    other_org = await make_organization(name="Rival Sales", slug="rival-sales")
    rival_store = await make_store(other_org, code="RIVALS")
    session.add(
        Sale(
            organization_id=other_org.id,
            store_id=rival_store.id,
            customer_id=customer_id,
            status=SaleStatus.COMPLETED,
            subtotal=Decimal("500.00"),
            total=Decimal("500.00"),
            idempotency_key=f"rival-{uuid4().hex}",
        )
    )
    await session.commit()

    summary = await client.get(f"/customers/{customer_id}/history", headers=headers)
    assert summary.status_code == 200
    assert summary.json()["data"]["saleCount"] == 0
    assert summary.json()["data"]["totalSpent"] == "0.00"


async def test_history_nets_returns_out_of_total_spent(
    client: Any, session: Any, tenant: dict[str, Any], auth_headers: Callable[..., dict[str, str]]
) -> None:
    """Lifetime spend is what the customer kept, not what they carried out.

    ``sale_returns.total`` is stored negative, so a 100.00 sale with a 40.00
    return is 60.00 of spend -- reading gross would overstate every refunded
    customer for the life of the account.
    """
    headers = auth_headers(tenant)
    data = await _create_customer(client, headers)
    customer_id = UUID(data["id"])

    sale = Sale(
        organization_id=tenant["organization"].id,
        store_id=tenant["store"].id,
        customer_id=customer_id,
        status=SaleStatus.COMPLETED,
        subtotal=Decimal("100.00"),
        total=Decimal("100.00"),
        idempotency_key=f"sale-{uuid4().hex}",
    )
    session.add(sale)
    await session.flush()
    session.add(
        SaleReturn(
            organization_id=tenant["organization"].id,
            store_id=tenant["store"].id,
            sale_id=sale.id,
            reason="Partial return",
            total=Decimal("-40.00"),
            idempotency_key=f"ret-{uuid4().hex}",
            created_at=utc_now(),
        )
    )
    await session.commit()

    summary = await client.get(f"/customers/{customer_id}/history", headers=headers)
    assert summary.status_code == 200
    body = summary.json()["data"]
    assert body["totalSpent"] == "60.00"
    assert body["totalRefunded"] == "40.00"
    assert body["saleCount"] == 1


async def test_cashier_cannot_see_lifetime_spend(
    client: Any,
    session: Any,
    tenant: dict[str, Any],
    auth_headers: Callable[..., dict[str, str]],
    make_user: Callable[..., Any],
    make_membership: Callable[..., Any],
) -> None:
    """Lifetime spend is a management figure; the outstanding due is not.

    A cashier must still see what the customer owes to take a payment, so the
    endpoint redacts rather than refuses -- refusing would break the till.
    """
    headers = auth_headers(tenant)
    data = await _create_customer(client, headers)
    customer_id = UUID(data["id"])
    session.add(
        Sale(
            organization_id=tenant["organization"].id,
            store_id=tenant["store"].id,
            customer_id=customer_id,
            status=SaleStatus.COMPLETED,
            subtotal=Decimal("250.00"),
            total=Decimal("250.00"),
            idempotency_key=f"sale-{uuid4().hex}",
        )
    )
    await session.commit()

    owner_view = await client.get(f"/customers/{customer_id}/history", headers=headers)
    assert owner_view.json()["data"]["totalSpent"] == "250.00"

    cashier = await make_user(phone="+8801766666666", display_name="Till Cashier")
    await make_membership(tenant["organization"], cashier, Role.CASHIER, tenant["store"])
    cashier_headers = await _role_headers(session, tenant, cashier, Role.CASHIER)

    cashier_view = await client.get(f"/customers/{customer_id}/history", headers=cashier_headers)
    assert cashier_view.status_code == 200
    body = cashier_view.json()["data"]
    assert body["totalSpent"] is None
    assert body["totalDue"] == "0.00"


# --- search -----------------------------------------------------------------------


async def test_search_finds_a_customer_by_any_dialing_form(
    client: Any, tenant: dict[str, Any], auth_headers: Callable[..., dict[str, str]]
) -> None:
    """Every dialing form a cashier might type has to find the stored customer.

    This works for a happy reason worth locking down: the country code ``880``
    ends in the same ``0`` that the local trunk prefix uses, so ``01711111111``
    really is a substring of ``+8801711111111``. Any future change to the stored
    format or to digit extraction would quietly break lookup by the commonest
    form on a prescription, and only this test would notice.
    """
    headers = auth_headers(tenant)
    await _create_customer(client, headers, name="Rahim Miya", normalizedPhone="01711111111")

    for query in ("01711111111", "+8801711111111", "8801711111111", "1711111111", "01711 111111"):
        response = await client.get("/customers", params={"q": query}, headers=headers)
        assert response.status_code == 200, (query, response.text)
        items = response.json()["data"]["items"]
        assert [item["name"] for item in items] == ["Rahim Miya"], (query, items)


async def test_search_excludes_deactivated_customers_by_default(
    client: Any, tenant: dict[str, Any], auth_headers: Callable[..., dict[str, str]]
) -> None:
    """A deactivated customer must not be selectable at the till."""
    headers = auth_headers(tenant)
    data = await _create_customer(client, headers, name="Gone Away", normalizedPhone="01311112222")
    assert (await client.delete(f"/customers/{data['id']}", headers=headers)).status_code == 200

    default_view = await client.get("/customers", params={"q": "Gone"}, headers=headers)
    assert default_view.json()["data"]["total"] == 0

    # Still reachable when explicitly asked for, so the row is not orphaned.
    including = await client.get(
        "/customers", params={"q": "Gone", "active": "false"}, headers=headers
    )
    assert including.json()["data"]["total"] == 1


async def test_search_filters_by_outstanding_due(
    client: Any, session: Any, tenant: dict[str, Any], auth_headers: Callable[..., dict[str, str]]
) -> None:
    """``hasDue`` has to narrow the result set, not merely order it.

    Chasing credit is the reason the filter exists: a shop with hundreds of accounts
    needs the four that owe, and a filter that returns everyone in a helpful order
    still makes the cashier read every row. The paged ``total`` is asserted alongside
    the items because a filter applied to the page query and forgotten on the count
    reports "3 of 40" and looks like a pagination bug rather than a missing predicate.
    """
    headers = auth_headers(tenant)
    debtor = await _create_customer(
        client, headers, name="Owes Money", normalizedPhone="01911110000"
    )
    settled = await _create_customer(
        client, headers, name="Paid Up", normalizedPhone="01911110001"
    )
    row = await session.get(Customer, UUID(debtor["id"]))
    row.due_balance = Decimal("250.00")
    await session.commit()

    owing = await client.get("/customers", params={"hasDue": "true"}, headers=headers)
    assert owing.status_code == 200, owing.text
    assert [item["id"] for item in owing.json()["data"]["items"]] == [debtor["id"]]
    assert owing.json()["data"]["total"] == 1

    clear = await client.get("/customers", params={"hasDue": "false"}, headers=headers)
    assert [item["id"] for item in clear.json()["data"]["items"]] == [settled["id"]]
    assert clear.json()["data"]["total"] == 1

    # Omitting the filter is not the same as ``hasDue=false``.
    everyone = await client.get("/customers", headers=headers)
    assert everyone.json()["data"]["total"] == 2


async def test_due_filter_composes_with_the_other_filters(
    client: Any, session: Any, tenant: dict[str, Any], auth_headers: Callable[..., dict[str, str]]
) -> None:
    """A deactivated debtor is still not selectable at the till.

    ``hasDue`` narrows within the existing scope rather than replacing it, so the
    active-only default and the name search both continue to apply. Replacing the
    scope would surface closed accounts on the collections list.
    """
    headers = auth_headers(tenant)
    gone = await _create_customer(client, headers, name="Absconded", normalizedPhone="01911112222")
    here = await _create_customer(client, headers, name="Still Here", normalizedPhone="01911113333")
    for data in (gone, here):
        row = await session.get(Customer, UUID(data["id"]))
        row.due_balance = Decimal("75.00")
    await session.commit()
    assert (await client.delete(f"/customers/{gone['id']}", headers=headers)).status_code == 200

    active_debtors = await client.get("/customers", params={"hasDue": "true"}, headers=headers)
    assert [item["id"] for item in active_debtors.json()["data"]["items"]] == [here["id"]]

    # Reachable when explicitly asked for, so the debt is never written off silently.
    including = await client.get(
        "/customers", params={"hasDue": "true", "active": "false"}, headers=headers
    )
    assert [item["id"] for item in including.json()["data"]["items"]] == [gone["id"]]

    narrowed = await client.get(
        "/customers", params={"hasDue": "true", "q": "Still"}, headers=headers
    )
    assert [item["id"] for item in narrowed.json()["data"]["items"]] == [here["id"]]


# --- phone races and corrections -----------------------------------------------------


async def test_concurrent_duplicate_phone_reports_conflict(
    client: Any,
    tenant: dict[str, Any],
    auth_headers: Callable[..., dict[str, str]],
    monkeypatch: Any,
) -> None:
    """Losing the insert race must be a 409, not a 500.

    Two tills registering the same walk-in both pass the pre-check, then the
    unique index decides. Patching the pre-check blind reproduces that
    interleaving deterministically.
    """
    headers = auth_headers(tenant)
    await _create_customer(client, headers, normalizedPhone="01911112222")

    async def blind(*_args: Any, **_kwargs: Any) -> None:
        return None

    monkeypatch.setattr("app.services.customers._find_by_phone", blind)
    response = await client.post(
        "/customers", json={"name": "Racer", "normalizedPhone": "01911112222"}, headers=headers
    )
    assert response.status_code == 409, response.text


async def test_update_can_correct_a_phone_number(
    client: Any, tenant: dict[str, Any], auth_headers: Callable[..., dict[str, str]]
) -> None:
    """A mistyped phone is the commonest correction; it must be fixable."""
    headers = auth_headers(tenant)
    data = await _create_customer(client, headers, normalizedPhone="01711110000")

    corrected = await client.patch(
        f"/customers/{data['id']}", json={"normalizedPhone": "01811110000"}, headers=headers
    )
    assert corrected.status_code == 200, corrected.text
    assert corrected.json()["data"]["normalizedPhone"] == "+8801811110000"


async def test_update_to_a_taken_phone_conflicts(
    client: Any, tenant: dict[str, Any], auth_headers: Callable[..., dict[str, str]]
) -> None:
    headers = auth_headers(tenant)
    first = await _create_customer(client, headers, normalizedPhone="01711110001")
    second = await _create_customer(client, headers, name="Other", normalizedPhone="01811110002")

    clash = await client.patch(
        f"/customers/{second['id']}",
        json={"normalizedPhone": first["normalizedPhone"]},
        headers=headers,
    )
    assert clash.status_code == 409, clash.text


# --- due reconciliation ---------------------------------------------------------------


async def test_due_balance_rebuilds_from_the_payment_ledger(
    client: Any, session: Any, tenant: dict[str, Any], auth_headers: Callable[..., dict[str, str]]
) -> None:
    """``due_balance`` is a projection, so recomputing it must be possible.

    Cross-cutting rule 2 makes the ledger authoritative: a balance that drifts
    (a crashed request, a hand-edited row) has to be recoverable from the
    ``due`` payments and the refunds against them, not just trusted.
    """
    headers = auth_headers(tenant)
    data = await _create_customer(client, headers, normalizedPhone="01611110000")
    customer_id = UUID(data["id"])

    sale = Sale(
        organization_id=tenant["organization"].id,
        store_id=tenant["store"].id,
        customer_id=customer_id,
        status=SaleStatus.COMPLETED,
        subtotal=Decimal("300.00"),
        total=Decimal("300.00"),
        idempotency_key=f"sale-{uuid4().hex}",
    )
    session.add(sale)
    await session.flush()
    due_payment = Payment(
        organization_id=tenant["organization"].id,
        store_id=tenant["store"].id,
        reference_type="sale",
        reference_id=sale.id,
        customer_id=customer_id,
        method=PaymentMethod.DUE,
        amount=Decimal("300.00"),
        status=PaymentStatus.CAPTURED,
        idempotency_key=f"pay-{uuid4().hex}",
        created_at=utc_now(),
    )
    session.add(due_payment)
    await session.flush()
    session.add(
        PaymentRefund(
            organization_id=tenant["organization"].id,
            store_id=tenant["store"].id,
            payment_id=due_payment.id,
            amount=Decimal("120.00"),
            idempotency_key=f"ref-{uuid4().hex}",
            created_at=utc_now(),
        )
    )

    # Drift the projection, exactly as a half-applied write would.
    customer = await session.get(Customer, customer_id)
    customer.due_balance = Decimal("999.00")
    await session.commit()

    rebuilt = await client.post(f"/customers/{customer_id}/due/rebuild", headers=headers)
    assert rebuilt.status_code == 200, rebuilt.text
    assert rebuilt.json()["data"]["dueBalance"] == "180.00"

    session.expunge_all()
    refreshed = await session.get(Customer, customer_id)
    assert Decimal(refreshed.due_balance) == Decimal("180.00")

    # Idempotent: a second rebuild agrees with the first.
    again = await client.post(f"/customers/{customer_id}/due/rebuild", headers=headers)
    assert again.json()["data"]["dueBalance"] == "180.00"


async def test_rebuild_due_requires_owner_or_manager(
    client: Any,
    session: Any,
    tenant: dict[str, Any],
    auth_headers: Callable[..., dict[str, str]],
    make_user: Callable[..., Any],
    make_membership: Callable[..., Any],
) -> None:
    headers = auth_headers(tenant)
    data = await _create_customer(client, headers)
    cashier = await make_user(phone="+8801755555555", display_name="Cashier Two")
    await make_membership(tenant["organization"], cashier, Role.CASHIER, tenant["store"])
    cashier_headers = await _role_headers(session, tenant, cashier, Role.CASHIER)

    denied = await client.post(f"/customers/{data['id']}/due/rebuild", headers=cashier_headers)
    assert denied.status_code == 403

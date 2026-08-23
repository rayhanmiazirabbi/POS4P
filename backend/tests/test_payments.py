from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select

from app.context import RequestContext
from app.domains.payments import PaymentStatus
from app.models import Role, StoreProduct


def _headers(tenant: dict[str, Any], *, role: Role = Role.OWNER) -> dict[str, str]:
    from tests.conftest import access_token_for

    token = access_token_for(
        session_id=tenant["session"].id,
        user_id=tenant["owner"].id,
        organization_id=tenant["organization"].id,
        role=role,
        store_id=tenant["store"].id,
    )
    return {"Authorization": f"Bearer {token}"}


async def _setup_sale(
    client: Any, session: Any, tenant: dict[str, Any], *, method: str = "cash"
) -> dict[str, Any]:
    """Create one product with stock and sell a single unit through the sales API."""
    from app.domains.products import PharmacyProduct
    from app.services.inventory import receive_batch

    product = PharmacyProduct(
        organization_id=tenant["organization"].id, name="Ibuprofen", unit="box", active=True
    )
    session.add(product)
    await session.flush()
    sp = StoreProduct(
        organization_id=tenant["organization"].id,
        store_id=tenant["store"].id,
        pharmacy_product_id=product.id,
        sku=f"SKU-PAY-{uuid4().hex[:6]}",
        sale_price=Decimal("10.00"),
        minimum_stock=Decimal(0),
        active=True,
    )
    session.add(sp)
    await session.flush()
    context = RequestContext(
        organization_id=tenant["organization"].id,
        user_id=tenant["owner"].id,
        role=Role.OWNER,
        store_id=tenant["store"].id,
    )
    await receive_batch(
        session,
        context,
        sp.id,
        batch_number=f"B-{uuid4().hex[:6]}",
        expiry_date=date.today() + timedelta(days=365),
        unit_cost=Decimal("5.00"),
        quantity=Decimal("10"),
        idempotency_key=f"recv-pay-{uuid4().hex[:8]}",
    )
    await session.commit()

    payment: dict[str, Any] = {"method": method, "amount": "10.00"}
    if method == "cash":
        payment["receivedAmount"] = "10.00"
    response = await client.post(
        "/sales",
        json={
            "items": [{"storeProductId": str(sp.id), "quantity": "1"}],
            "payments": [payment],
        },
        headers={**_headers(tenant), "Idempotency-Key": f"pay-sale-{uuid4().hex[:12]}"},
    )
    assert response.status_code == 201, response.text
    return response.json()["data"]


async def test_payments_reference_the_sale_and_list_filters(
    client: Any, session: Any, tenant: dict[str, Any]
) -> None:
    data = await _setup_sale(client, session, tenant)
    payment = data["payments"][0]
    assert payment["referenceType"] == "sale"
    assert payment["referenceId"] == data["id"]
    assert payment["status"] == PaymentStatus.CAPTURED.value

    listing = await client.get(
        f"/payments?referenceType=sale&referenceId={data['id']}", headers=_headers(tenant)
    )
    assert listing.status_code == 200
    page = listing.json()["data"]
    assert page["total"] == 1
    assert page["items"][0]["id"] == payment["id"]

    detail = await client.get(f"/payments/{payment['id']}", headers=_headers(tenant))
    assert detail.status_code == 200
    assert detail.json()["data"]["amount"] == "10.00"


async def test_manual_status_update_owner_only_and_transitions(
    client: Any, session: Any, tenant: dict[str, Any]
) -> None:
    from datetime import datetime as dt

    from app.domains.payments import Payment

    data = await _setup_sale(client, session, tenant)
    payment_id = data["payments"][0]["id"]

    # Force the payment into pending to exercise the manual transition path.
    pending = Payment(
        organization_id=tenant["organization"].id,
        store_id=tenant["store"].id,
        reference_type="sale",
        reference_id=UUID(data["id"]),
        method="bkash",
        amount=Decimal("5.00"),
        status=PaymentStatus.PENDING,
        idempotency_key=f"pending-{uuid4().hex[:10]}",
        created_at=dt.now(),
    )
    session.add(pending)
    await session.commit()

    captured = await client.post(
        f"/payments/{pending.id}/status",
        json={"status": "captured"},
        headers=_headers(tenant),
    )
    assert captured.status_code == 200, captured.text
    assert captured.json()["data"]["status"] == "captured"

    done = await client.post(
        f"/payments/{pending.id}/status",
        json={"status": "failed"},
        headers=_headers(tenant),
    )
    assert done.status_code == 409

    # A completed (captured) payment can no longer be resolved manually.
    again = await client.post(
        f"/payments/{payment_id}/status",
        json={"status": "failed"},
        headers=_headers(tenant),
    )
    assert again.status_code == 409


async def test_payment_of_other_tenant_is_not_found(
    client: Any, session: Any, tenant: dict[str, Any]
) -> None:
    data = await _setup_sale(client, session, tenant)
    payment_id = data["payments"][0]["id"]

    from datetime import timedelta

    from app.models import Organization, Session as SessionModel
    from app.security import generate_token, hash_token, utc_now
    from tests.conftest import access_token_for

    other_org = Organization(name="Rival", slug="rival-payments", settings={})
    session.add(other_org)
    await session.flush()
    from app.models import User

    rival_user = User(phone="+8801799999999", display_name="Rival")
    session.add(rival_user)
    await session.flush()
    from app.models import OrganizationUser

    session.add(
        OrganizationUser(
            organization_id=other_org.id, user_id=rival_user.id, role=Role.OWNER, active=True
        )
    )
    await session.flush()
    auth_session = SessionModel(
        user_id=rival_user.id,
        organization_id=other_org.id,
        store_id=None,
        refresh_token_hash=hash_token(generate_token()),
        expires_at=utc_now() + timedelta(days=30),
    )
    session.add(auth_session)
    await session.commit()
    rival_headers = {
        "Authorization": f"Bearer {access_token_for(session_id=auth_session.id, user_id=rival_user.id, organization_id=other_org.id, role=Role.OWNER, store_id=None)}"
    }

    response = await client.get(f"/payments/{payment_id}", headers=rival_headers)
    assert response.status_code == 404

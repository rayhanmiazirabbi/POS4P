from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Any
from uuid import uuid4

from app.context import RequestContext
from app.models import Role
from tests.conftest import access_token_for


def _headers(tenant: dict[str, Any]) -> dict[str, str]:
    token = access_token_for(
        session_id=tenant["session"].id,
        user_id=tenant["owner"].id,
        organization_id=tenant["organization"].id,
        role=Role.OWNER,
        store_id=tenant["store"].id,
    )
    return {"Authorization": f"Bearer {token}"}


async def _saleable_product(session: Any, tenant: dict[str, Any]) -> Any:
    from app.domains.products import PharmacyProduct, StoreProduct
    from app.services.inventory import receive_batch

    product = PharmacyProduct(organization_id=tenant["organization"].id, name="Padex", unit="box", active=True)
    session.add(product)
    await session.flush()
    store_product = StoreProduct(
        organization_id=tenant["organization"].id,
        store_id=tenant["store"].id,
        pharmacy_product_id=product.id,
        sku=f"SKU-{uuid4().hex[:8]}",
        sale_price=Decimal("10.00"),
        minimum_stock=Decimal(0),
        active=True,
    )
    session.add(store_product)
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
        store_product.id,
        batch_number="B1",
        expiry_date=date.today() + timedelta(days=100),
        unit_cost=Decimal(5),
        quantity=Decimal("10"),
        idempotency_key=f"recv-{uuid4().hex[:12]}",
    )
    await session.commit()
    return store_product


async def _configure(client: Any, tenant: dict[str, Any], methods: list[dict[str, Any]]) -> Any:
    return await client.patch(
        "/organizations/current/settings",
        json={"paymentMethods": methods},
        headers=_headers(tenant),
    )


async def test_settings_roundtrip_and_validation(client: Any, tenant: dict[str, Any]) -> None:
    update = await _configure(client, tenant, [
        {"value": "bkash", "label": "bKash", "active": True},
        {"value": "rocket", "label": "Rocket", "active": True},
    ])
    assert update.status_code == 200, update.text
    methods = update.json()["data"]["settings"]["paymentMethods"]
    assert [m["value"] for m in methods] == ["bkash", "rocket"]

    read = await client.get("/organizations/current/settings", headers=_headers(tenant))
    assert [m["label"] for m in read.json()["data"]["settings"]["paymentMethods"]] == ["bKash", "Rocket"]

    reserved = await _configure(client, tenant, [{"value": "cash", "label": "Cash"}])
    assert reserved.status_code == 422
    duplicate = await _configure(client, tenant, [
        {"value": "rocket", "label": "Rocket"},
        {"value": "rocket", "label": "Rocket again"},
    ])
    assert duplicate.status_code == 422


async def test_defaults_when_never_configured(client: Any, tenant: dict[str, Any]) -> None:
    read = await client.get("/organizations/current/settings", headers=_headers(tenant))
    methods = read.json()["data"]["settings"]["paymentMethods"]
    assert [m["value"] for m in methods] == ["bkash", "nagad"]


async def test_sale_accepts_configured_method_and_refuses_unknown(
    client: Any, session: Any, tenant: dict[str, Any]
) -> None:
    sp = await _saleable_product(session, tenant)

    unknown = await client.post(
        "/sales",
        json={
            "items": [{"storeProductId": str(sp.id), "quantity": "1"}],
            "payments": [{"method": "rocket", "amount": "10.00"}],
        },
        headers={**_headers(tenant), "Idempotency-Key": f"rocket-denied-{uuid4().hex}"[:36]},
    )
    assert unknown.status_code == 422, unknown.text

    assert (await _configure(client, tenant, [{"value": "rocket", "label": "Rocket"}])).status_code == 200

    sale = await client.post(
        "/sales",
        json={
            "items": [{"storeProductId": str(sp.id), "quantity": "1"}],
            "payments": [{"method": "rocket", "amount": "10.00"}],
        },
        headers={**_headers(tenant), "Idempotency-Key": f"rocket-sale-{uuid4().hex}"[:36]},
    )
    assert sale.status_code == 201, sale.text
    assert sale.json()["data"]["payments"][0]["method"] == "rocket"


async def test_inactive_method_is_refused(client: Any, session: Any, tenant: dict[str, Any]) -> None:
    sp = await _saleable_product(session, tenant)
    assert (
        await _configure(client, tenant, [{"value": "bkash", "label": "bKash", "active": False}])
    ).status_code == 200
    sale = await client.post(
        "/sales",
        json={
            "items": [{"storeProductId": str(sp.id), "quantity": "1"}],
            "payments": [{"method": "bkash", "amount": "10.00"}],
        },
        headers={**_headers(tenant), "Idempotency-Key": f"inactive-{uuid4().hex}"[:36]},
    )
    assert sale.status_code == 422


async def test_return_refunds_a_configured_method(
    client: Any, session: Any, tenant: dict[str, Any]
) -> None:
    from sqlalchemy import select

    from app.domains.payments import PaymentRefund

    sp = await _saleable_product(session, tenant)
    assert (await _configure(client, tenant, [{"value": "rocket", "label": "Rocket"}])).status_code == 200
    sale = await client.post(
        "/sales",
        json={
            "items": [{"storeProductId": str(sp.id), "quantity": "2"}],
            "payments": [{"method": "rocket", "amount": "20.00"}],
        },
        headers={**_headers(tenant), "Idempotency-Key": f"rocket-r-{uuid4().hex}"[:36]},
    )
    assert sale.status_code == 201, sale.text
    body = sale.json()["data"]

    returned = await client.post(
        f"/sales/{body['id']}/returns",
        json={"reason": "wrong brand", "lines": [{"saleItemId": body["items"][0]["id"], "quantity": "2"}]},
        headers={**_headers(tenant), "Idempotency-Key": f"rocket-ret-{uuid4().hex}"[:36]},
    )
    assert returned.status_code == 201, returned.text
    assert returned.json()["data"]["total"] == "-20.00"

    refunds = list(await session.scalars(select(PaymentRefund)))
    assert len(refunds) == 1
    assert Decimal(refunds[0].amount) == Decimal("20.00")

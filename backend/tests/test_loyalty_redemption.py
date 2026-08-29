from __future__ import annotations

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


async def _customer(session: Any, tenant: dict[str, Any]) -> Any:
    from app.domains.customers import Customer

    customer = Customer(
        organization_id=tenant["organization"].id,
        name="Points Holder",
        due_balance=Decimal(0),
        advance_balance=Decimal(0),
        preferences={},
        active=True,
    )
    session.add(customer)
    await session.commit()
    return customer


async def _product(session: Any, tenant: dict[str, Any]) -> Any:
    from datetime import date, timedelta

    from app.domains.products import PharmacyProduct, StoreProduct
    from app.services.inventory import receive_batch

    product = PharmacyProduct(organization_id=tenant["organization"].id, name="Seclo", unit="box", active=True)
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
        quantity=Decimal("20"),
        idempotency_key=f"recv-{uuid4().hex[:12]}",
    )
    await session.commit()
    return store_product


async def _seed_points(client: Any, tenant: dict[str, Any], customer_id: str, points: int) -> dict[str, Any]:
    enroll = await client.post("/loyalty/accounts", json={"customerId": customer_id}, headers=_headers(tenant))
    assert enroll.status_code == 201, enroll.text
    account = enroll.json()["data"]
    if points > 0:
        earn = await client.post(
            f"/loyalty/accounts/{account['id']}/transactions",
            params={"idempotencyKey": f"seed-{uuid4().hex}"[:40]},
            json={"transactionType": "bonus", "points": points, "sourceType": "test", "sourceId": str(uuid4())},
            headers=_headers(tenant),
        )
        assert earn.status_code == 200, earn.text
    refreshed = await client.get(f"/loyalty/accounts/{account['id']}", headers=_headers(tenant))
    return refreshed.json()["data"]


async def test_redemption_pays_part_of_the_sale_and_deducts_points(
    client: Any, session: Any, tenant: dict[str, Any]
) -> None:
    customer = await _customer(session, tenant)
    sp = await _product(session, tenant)
    account = await _seed_points(client, tenant, str(customer.id), 120)
    assert account["balance"] == 120

    sale = await client.post(
        "/sales",
        json={
            "customerId": str(customer.id),
            "items": [{"storeProductId": str(sp.id), "quantity": "5"}],
            "loyaltyRedemption": {"points": 30},
            "payments": [{"method": "cash", "amount": "20.00", "receivedAmount": "20.00"}],
        },
        headers={**_headers(tenant), "Idempotency-Key": f"redeem-{uuid4().hex}"[:36]},
    )
    assert sale.status_code == 201, sale.text
    body = sale.json()["data"]
    # 30 points x 1.00 = 30.00 credit; 50.00 total leaves 20.00 to tender.
    assert body["loyaltyPointsRedeemed"] == 30
    assert Decimal(body["loyaltyCredit"]) == Decimal("30.00")
    # What the tenders must cover after the credit: 50.00 - 30.00.
    assert Decimal(body["amountDueNow"]) == Decimal("20.00")
    assert body["loyaltyBalanceAfter"] == 90

    read = await client.get(f"/loyalty/accounts/{account['id']}", headers=_headers(tenant))
    assert read.json()["data"]["balance"] == 90


async def test_redemption_refusals(
    client: Any, session: Any, tenant: dict[str, Any]
) -> None:
    customer = await _customer(session, tenant)
    sp = await _product(session, tenant)

    no_customer = await client.post(
        "/sales",
        json={
            "items": [{"storeProductId": str(sp.id), "quantity": "1"}],
            "loyaltyRedemption": {"points": 10},
            "payments": [{"method": "cash", "amount": "10.00", "receivedAmount": "10.00"}],
        },
        headers={**_headers(tenant), "Idempotency-Key": f"nocust-{uuid4().hex}"[:36]},
    )
    assert no_customer.status_code == 422

    account = await _seed_points(client, tenant, str(customer.id), 5)
    not_enough = await client.post(
        "/sales",
        json={
            "customerId": str(customer.id),
            "items": [{"storeProductId": str(sp.id), "quantity": "1"}],
            "loyaltyRedemption": {"points": 10},
            "payments": [{"method": "cash", "amount": "10.00", "receivedAmount": "10.00"}],
        },
        headers={**_headers(tenant), "Idempotency-Key": f"broke-{uuid4().hex}"[:36]},
    )
    assert not_enough.status_code == 422

    # Balance enough, but the credit would exceed the whole sale: 50 points
    # pay 50.00 against a 10.00 basket.
    top_up = await client.post(
        f"/loyalty/accounts/{account['id']}/transactions",
        params={"idempotencyKey": f"rich-{uuid4().hex}"[:40]},
        json={"transactionType": "bonus", "points": 200, "sourceType": "test", "sourceId": str(uuid4())},
        headers=_headers(tenant),
    )
    assert top_up.status_code == 200
    too_much = await client.post(
        "/sales",
        json={
            "customerId": str(customer.id),
            "items": [{"storeProductId": str(sp.id), "quantity": "1"}],
            "loyaltyRedemption": {"points": 50},
            "payments": [{"method": "cash", "amount": "10.00", "receivedAmount": "10.00"}],
        },
        headers={**_headers(tenant), "Idempotency-Key": f"toomuch-{uuid4().hex}"[:36]},
    )
    assert too_much.status_code == 422


async def test_return_and_void_restore_points(
    client: Any, session: Any, tenant: dict[str, Any]
) -> None:
    customer = await _customer(session, tenant)
    sp = await _product(session, tenant)
    account = await _seed_points(client, tenant, str(customer.id), 200)

    # Two units at 10.00; redeem 15 points (15.00 credit), pay the 5.00 rest in cash.
    sale = await client.post(
        "/sales",
        json={
            "customerId": str(customer.id),
            "items": [{"storeProductId": str(sp.id), "quantity": "2"}],
            "loyaltyRedemption": {"points": 15},
            "payments": [{"method": "cash", "amount": "5.00", "receivedAmount": "5.00"}],
        },
        headers={**_headers(tenant), "Idempotency-Key": f"restore-{uuid4().hex}"[:36]},
    )
    assert sale.status_code == 201, sale.text
    body = sale.json()["data"]
    assert body["loyaltyBalanceAfter"] == 185

    half = await client.post(
        f"/sales/{body['id']}/returns",
        json={"reason": "one back", "lines": [{"saleItemId": body["items"][0]["id"], "quantity": "1"}]},
        headers={**_headers(tenant), "Idempotency-Key": f"halfret-{uuid4().hex}"[:36]},
    )
    assert half.status_code == 201, half.text
    # 15 points x (10.00 refund / 20.00 total) = 7 back (floored).
    after_half = await client.get(f"/loyalty/accounts/{account['id']}", headers=_headers(tenant))
    assert after_half.json()["data"]["balance"] == 192

    # A separate sale, voided whole: every point it burned comes back.
    void_sale = await client.post(
        "/sales",
        json={
            "customerId": str(customer.id),
            "items": [{"storeProductId": str(sp.id), "quantity": "2"}],
            "loyaltyRedemption": {"points": 15},
            "payments": [{"method": "cash", "amount": "5.00", "receivedAmount": "5.00"}],
        },
        headers={**_headers(tenant), "Idempotency-Key": f"voidsrc-{uuid4().hex}"[:36]},
    )
    assert void_sale.status_code == 201, void_sale.text
    void_body = void_sale.json()["data"]
    assert void_body["loyaltyBalanceAfter"] == 177

    voided = await client.post(
        f"/sales/{void_body['id']}/void",
        json={"reason": "whole thing was wrong"},
        headers=_headers(tenant),
    )
    assert voided.status_code == 200, voided.text
    after_void = await client.get(f"/loyalty/accounts/{account['id']}", headers=_headers(tenant))
    assert after_void.json()["data"]["balance"] == 192

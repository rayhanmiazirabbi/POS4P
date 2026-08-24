from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.context import RequestContext
from app.domains.catalog import CatalogProduct
from app.domains.ecommerce import EcommerceProductSetting, Storefront
from app.domains.inventory import InventoryBalance, InventoryBatch
from app.domains.orders import OrderStatus
from app.domains.prescriptions import PrescriptionStatus
from app.domains.products import PharmacyProduct, StoreProduct
from app.domains.sales import Sale, SaleChannel
from app.models import Role
from app.services.inventory import receive_batch
from tests.conftest import access_token_for

ORDER_KEY = "order-key-0000000000001"


def _headers(tenant: dict[str, Any], *, role: Role = Role.OWNER) -> dict[str, str]:
    token = access_token_for(
        session_id=tenant["session"].id,
        user_id=tenant["owner"].id,
        organization_id=tenant["organization"].id,
        role=role,
        store_id=tenant["store"].id,
    )
    return {"Authorization": f"Bearer {token}"}


async def _make_catalog_product(
    session: Any,
    tenant: dict[str, Any],
    *,
    prescription_required: bool = False,
) -> CatalogProduct:
    product = CatalogProduct(
        name=f"Rx Product {uuid4().hex[:6]}",
        package_size=Decimal(1),
        package_unit="box",
        prescription_required=prescription_required,
        country_code="BD",
    )
    session.add(product)
    await session.flush()
    return product


async def _make_store_product(
    session: Any,
    tenant: dict[str, Any],
    tenant_headers: dict[str, str],
    client: AsyncClient,
    *,
    sku: str | None = None,
    price: str = "10.00",
    prescription_required: bool = False,
) -> StoreProduct:
    catalog = await _make_catalog_product(
        session, tenant, prescription_required=prescription_required
    )
    pharmacy_product = PharmacyProduct(
        organization_id=tenant["organization"].id,
        name=f"Product {sku or uuid4().hex[:6]}",
        unit="box",
        active=True,
        catalog_product_id=catalog.id,
    )
    session.add(pharmacy_product)
    await session.flush()
    store_product = StoreProduct(
        organization_id=tenant["organization"].id,
        store_id=tenant["store"].id,
        pharmacy_product_id=pharmacy_product.id,
        sku=sku or f"SKU-{uuid4().hex[:8]}",
        sale_price=Decimal(price),
        minimum_stock=Decimal(0),
        active=True,
    )
    session.add(store_product)
    await session.commit()

    # List it online so orders can reference it.
    response = await client.put(
        f"/ecommerce/products/{store_product.id}/listing",
        json={"listed": True},
        headers=tenant_headers,
    )
    assert response.status_code == 200, response.text
    return store_product


async def _receive(
    session: Any,
    tenant: dict[str, Any],
    store_product_id: Any,
    *,
    quantity: str = "10",
    expiry_days: int = 100,
) -> None:
    context = RequestContext(
        organization_id=tenant["organization"].id,
        user_id=tenant["owner"].id,
        role=Role.OWNER,
        store_id=tenant["store"].id,
    )
    await receive_batch(
        session,
        context,
        store_product_id,
        batch_number=f"B-{uuid4().hex[:8]}",
        expiry_date=date.today() + timedelta(days=expiry_days),
        unit_cost=Decimal("5.00"),
        quantity=Decimal(quantity),
        idempotency_key=f"recv-{uuid4()}",
    )
    await session.commit()


async def _balance(session: Any, store_product_id: Any) -> InventoryBalance:
    return await session.scalar(
        select(InventoryBalance).where(
            InventoryBalance.store_product_id == store_product_id
        )
    )


# --- ecommerce --------------------------------------------------------------


async def test_listing_enables_store_product_online_without_copying_data(
    client: AsyncClient, session: Any, tenant: dict[str, Any]
) -> None:
    headers = _headers(tenant)
    store_product = await _make_store_product(session, tenant, headers, client)

    response = await client.put(
        f"/ecommerce/products/{store_product.id}/listing",
        json={
            "onlineName": "Paracetamol 500mg Online",
            "onlinePrice": "12.50",
            "listed": True,
            "deliveryEnabled": True,
        },
        headers=headers,
    )
    assert response.status_code == 200, response.text
    body = response.json()["data"]
    assert body["onlinePrice"] == "12.50"
    assert body["listed"] is True

    # The POS record keeps its own price; the online value is only an overlay.
    await session.refresh(store_product)
    assert Decimal(store_product.sale_price) == Decimal("10.00")

    await client.post(
        "/ecommerce/storefronts",
        json={"slug": "main-shop", "displayName": "Main Shop", "enabled": True},
        headers=headers,
    )
    catalogue = (
        await client.get("/ecommerce/storefronts/main-shop/catalogue", headers=headers)
    ).json()["data"]
    assert len(catalogue) == 1
    assert catalogue[0]["name"] == "Paracetamol 500mg Online"
    assert Decimal(catalogue[0]["price"]) == Decimal("12.50")


async def test_unlisted_products_do_not_appear_in_public_catalogue(
    client: AsyncClient, session: Any, tenant: dict[str, Any]
) -> None:
    headers = _headers(tenant)
    unlisted = await _make_store_product(session, tenant, headers, client)
    listed = await _make_store_product(session, tenant, headers, client)
    delisted = await client.put(
        f"/ecommerce/products/{unlisted.id}/listing",
        json={"listed": False},
        headers=headers,
    )
    assert delisted.status_code == 200, delisted.text

    await client.post(
        "/ecommerce/storefronts",
        json={"slug": "sf", "displayName": "SF", "enabled": True},
        headers=headers,
    )
    catalogue = (
        await client.get("/ecommerce/storefronts/sf/catalogue", headers=headers)
    ).json()["data"]
    assert [item["storeProductId"] for item in catalogue] == [str(listed.id)]


async def test_cashier_cannot_manage_listings(
    client: AsyncClient, session: Any, tenant: dict[str, Any]
) -> None:
    from tests._phase4_helpers import role_headers

    cashier_headers = await role_headers(session, tenant, Role.CASHIER)
    response = await client.post(
        "/ecommerce/storefronts",
        json={"slug": "nope", "displayName": "Nope"},
        headers=cashier_headers,
    )
    assert response.status_code == 403


async def test_disabled_storefront_is_not_resolvable(
    client: AsyncClient, session: Any, tenant: dict[str, Any]
) -> None:
    headers = _headers(tenant)
    await client.post(
        "/ecommerce/storefronts",
        json={"slug": "dark", "displayName": "Dark", "enabled": False},
        headers=headers,
    )
    response = await client.get("/ecommerce/storefronts/dark/catalogue", headers=headers)
    assert response.status_code == 404


# --- orders -----------------------------------------------------------------


async def _checkout(
    client: AsyncClient,
    headers: dict[str, str],
    items: list[dict],
    *,
    key: str = ORDER_KEY,
    customer_id: str | None = None,
) -> Any:
    payload: dict[str, Any] = {"items": items}
    if customer_id:
        payload["customerId"] = customer_id
    return await client.post(
        "/orders", json=payload, headers={**headers, "Idempotency-Key": key}
    )


async def test_guest_order_reserves_stock_and_replays_idempotently(
    client: AsyncClient, session: Any, tenant: dict[str, Any]
) -> None:
    headers = _headers(tenant)
    product = await _make_store_product(session, tenant, headers, client)
    await _receive(session, tenant, product.id)

    first = await _checkout(client, headers, [{"storeProductId": str(product.id), "quantity": "3"}])
    assert first.status_code == 201, first.text
    body = first.json()["data"]
    assert body["status"] == "reserved"
    assert body["customerId"] is None
    order_id = body["id"]

    balance = await _balance(session, product.id)
    assert Decimal(balance.reserved) == Decimal("3")

    replayed = await _checkout(
        client, headers, [{"storeProductId": str(product.id), "quantity": "3"}]
    )
    assert replayed.status_code == 201
    assert replayed.json()["data"]["id"] == order_id

    conflicting = await _checkout(
        client,
        headers,
        [{"storeProductId": str(product.id), "quantity": "5"}],
    )
    assert conflicting.status_code == 409


async def test_order_with_insufficient_stock_is_rejected(
    client: AsyncClient, session: Any, tenant: dict[str, Any]
) -> None:
    headers = _headers(tenant)
    product = await _make_store_product(session, tenant, headers, client)
    await _receive(session, tenant, product.id, quantity="1")

    response = await _checkout(
        client, headers, [{"storeProductId": str(product.id), "quantity": "5"}]
    )
    assert response.status_code == 409


async def test_cancelled_order_releases_reservations(
    client: AsyncClient, session: Any, tenant: dict[str, Any]
) -> None:
    headers = _headers(tenant)
    product = await _make_store_product(session, tenant, headers, client)
    await _receive(session, tenant, product.id)

    created = await _checkout(
        client, headers, [{"storeProductId": str(product.id), "quantity": "2"}]
    )
    order_id = created.json()["data"]["id"]

    cancelled = await client.post(
        f"/orders/{order_id}/transition",
        json={"status": "cancelled"},
        headers=headers,
    )
    assert cancelled.status_code == 200, cancelled.text
    balance = await _balance(session, product.id)
    assert Decimal(balance.reserved) == Decimal("0")

    again = await client.post(
        f"/orders/{order_id}/transition",
        json={"status": "reserved"},
        headers=headers,
    )
    assert again.status_code == 409


async def test_prescription_required_order_gates_acceptance_then_completes_to_sale(
    client: AsyncClient, session: Any, tenant: dict[str, Any]
) -> None:
    headers = _headers(tenant)
    product = await _make_store_product(
        session, tenant, headers, client, prescription_required=True
    )
    await _receive(session, tenant, product.id)

    created = await _checkout(
        client, headers, [{"storeProductId": str(product.id), "quantity": "2"}], key=ORDER_KEY + "-rx"
    )
    assert created.status_code == 201, created.text
    body = created.json()["data"]
    assert body["prescriptionRequired"] is True or body["prescription_required"] is True
    order_id = body["id"]
    order_id_obj = UUID(order_id)

    blocked = await client.post(
        f"/orders/{order_id}/transition", json={"status": "accepted"}, headers=headers
    )
    assert blocked.status_code == 409

    prescription = await client.post(
        "/prescriptions",
        json={"prescriberName": "Dr. Rahman"},
        headers=headers,
    )
    assert prescription.status_code == 201, prescription.text
    prescription_id = prescription.json()["data"]["id"]
    approved = await client.post(
        f"/prescriptions/{prescription_id}/review",
        json={"status": "approved", "notes": "valid"},
        headers=headers,
    )
    assert approved.status_code == 200, approved.text

    from app.domains.prescriptions import Prescription

    linked = await client.post(
        f"/prescriptions/{prescription_id}/order",
        json={"orderId": order_id},
        headers=headers,
    )
    assert linked.status_code == 200, linked.text
    assert linked.json()["data"]["orderId"] == order_id
    prescription_row = await session.get(Prescription, UUID(prescription_id))
    assert prescription_row.order_id == UUID(order_id)

    accepted = await client.post(
        f"/orders/{order_id}/transition", json={"status": "accepted"}, headers=headers
    )
    assert accepted.status_code == 200, accepted.text

    for next_status in ("preparing", "ready", "completed"):
        stepped = await client.post(
            f"/orders/{order_id}/transition", json={"status": next_status}, headers=headers
        )
        assert stepped.status_code == 200, stepped.text
    completed = stepped

    sale = await session.scalar(select(Sale).where(Sale.order_id == order_id_obj))
    assert sale is not None
    assert sale.channel is SaleChannel.ONLINE
    assert Decimal(sale.total) == Decimal("20.00")
    balance = await _balance(session, product.id)
    assert Decimal(balance.on_hand) == Decimal("8")
    assert Decimal(balance.reserved) == Decimal("0")

    duplicate = await client.post(
        f"/orders/{order_id}/transition", json={"status": "completed"}, headers=headers
    )
    assert duplicate.status_code == 409


async def test_order_of_another_organization_is_invisible(
    client: AsyncClient, session: Any, tenant: dict[str, Any], make_organization: Callable, make_user: Callable, make_store: Callable, make_membership: Callable
) -> None:
    other_org = await make_organization(slug="other-org")
    other_owner = await make_user(phone="+8801700000099")
    other_store = await make_store(other_org)
    await make_membership(other_org, other_owner, Role.OWNER, other_store)

    headers = _headers(tenant)
    product = await _make_store_product(session, tenant, headers, client)
    await _receive(session, tenant, product.id)
    created = await _checkout(
        client, headers, [{"storeProductId": str(product.id), "quantity": "1"}], key=ORDER_KEY + "-iso"
    )
    order_id = created.json()["data"]["id"]

    from datetime import timedelta

    from app.models import Session as SessionModel
    from app.security import generate_token, hash_token, utc_now

    other_auth_session = SessionModel(
        user_id=other_owner.id,
        organization_id=other_org.id,
        store_id=other_store.id,
        refresh_token_hash=hash_token(generate_token()),
        expires_at=utc_now() + timedelta(days=30),
    )
    session.add(other_auth_session)
    await session.commit()

    token = access_token_for(
        session_id=other_auth_session.id,
        user_id=other_owner.id,
        organization_id=other_org.id,
        role=Role.OWNER,
        store_id=other_store.id,
    )
    intruder = {"Authorization": f"Bearer {token}"}
    assert (await client.get(f"/orders/{order_id}", headers=intruder)).status_code == 404
    transition = await client.post(
        f"/orders/{order_id}/transition", json={"status": "cancelled"}, headers=intruder
    )
    assert transition.status_code == 404


async def test_second_order_cannot_overreserve_stock_held_by_the_first(
    client: AsyncClient, session: Any, tenant: dict[str, Any]
) -> None:
    """Two checkouts racing for the same units must not drive available negative."""
    headers = _headers(tenant)
    product = await _make_store_product(session, tenant, headers, client)
    await _receive(session, tenant, product.id, quantity="5")

    first = await _checkout(
        client,
        headers,
        [{"storeProductId": str(product.id), "quantity": "4"}],
        key=ORDER_KEY + "-race-a",
    )
    assert first.status_code == 201, first.text

    second = await _checkout(
        client,
        headers,
        [{"storeProductId": str(product.id), "quantity": "4"}],
        key=ORDER_KEY + "-race-b",
    )
    assert second.status_code == 409
    assert second.json()["code"] == "INSUFFICIENT_STOCK"

    balance = await _balance(session, product.id)
    assert Decimal(balance.reserved) == Decimal("4")
    assert Decimal(balance.on_hand) - Decimal(balance.reserved) >= 0

    cancelled = await client.post(
        f"/orders/{first.json()['data']['id']}/transition",
        json={"status": "cancelled"},
        headers=headers,
    )
    assert cancelled.status_code == 200, cancelled.text

    freed = await _checkout(
        client,
        headers,
        [{"storeProductId": str(product.id), "quantity": "4"}],
        key=ORDER_KEY + "-race-c",
    )
    assert freed.status_code == 201, freed.text


async def test_checkout_allocates_around_batches_already_held_by_other_orders(
    client: AsyncClient, session: Any, tenant: dict[str, Any]
) -> None:
    """A hold on the FEFO batch must push later allocation to the next batch."""
    headers = _headers(tenant)
    product = await _make_store_product(session, tenant, headers, client)
    await _receive(session, tenant, product.id, quantity="10", expiry_days=5)
    await _receive(session, tenant, product.id, quantity="10", expiry_days=50)

    first = await _checkout(
        client,
        headers,
        [{"storeProductId": str(product.id), "quantity": "10"}],
        key=ORDER_KEY + "-fe-a",
    )
    assert first.status_code == 201, first.text

    second = await _checkout(
        client,
        headers,
        [{"storeProductId": str(product.id), "quantity": "10"}],
        key=ORDER_KEY + "-fe-b",
    )
    assert second.status_code == 201, second.text

    balance = await _balance(session, product.id)
    assert Decimal(balance.reserved) == Decimal("20")
    assert Decimal(balance.on_hand) - Decimal(balance.reserved) >= 0


async def test_prescription_links_to_order_only_through_the_api(
    client: AsyncClient, session: Any, tenant: dict[str, Any]
) -> None:
    headers = _headers(tenant)
    product = await _make_store_product(session, tenant, headers, client)
    await _receive(session, tenant, product.id)
    created = await _checkout(
        client,
        headers,
        [{"storeProductId": str(product.id), "quantity": "1"}],
        key=ORDER_KEY + "-rxlink",
    )
    assert created.status_code == 201, created.text
    order_id = created.json()["data"]["id"]

    made_linked = await client.post(
        "/prescriptions",
        json={"prescriberName": "Dr. Ahsan", "orderId": order_id},
        headers=headers,
    )
    assert made_linked.status_code == 201, made_linked.text
    assert made_linked.json()["data"]["orderId"] == order_id

    unknown_order = await client.post(
        "/prescriptions",
        json={"orderId": str(uuid4())},
        headers=headers,
    )
    assert unknown_order.status_code == 404

    made = await client.post("/prescriptions", json={}, headers=headers)
    prescription_id = made.json()["data"]["id"]
    attached_unknown = await client.post(
        f"/prescriptions/{prescription_id}/order",
        json={"orderId": str(uuid4())},
        headers=headers,
    )
    assert attached_unknown.status_code == 404


# --- anonymous storefront ----------------------------------------------------


async def test_anonymous_storefront_lists_and_guest_checks_out(
    client: AsyncClient, session: Any, tenant: dict[str, Any]
) -> None:
    """No bearer token anywhere: catalogue browsing and checkout are public."""
    headers = _headers(tenant)
    product = await _make_store_product(session, tenant, headers, client)
    await _receive(session, tenant, product.id)
    made = await client.post(
        "/ecommerce/storefronts",
        json={"slug": "public-shop", "displayName": "Public Shop", "enabled": True},
        headers=headers,
    )
    assert made.status_code == 201, made.text

    catalogue = await client.get(
        f"/storefronts/{tenant['organization'].slug}/public-shop/catalogue"
    )
    assert catalogue.status_code == 200, catalogue.text
    assert catalogue.json()["requestId"]
    assert [item["storeProductId"] for item in catalogue.json()["data"]] == [str(product.id)]

    guest = await client.post(
        f"/storefronts/{tenant['organization'].slug}/public-shop/orders",
        json={"items": [{"storeProductId": str(product.id), "quantity": "2"}]},
        headers={"Idempotency-Key": ORDER_KEY + "-anon"},
    )
    assert guest.status_code == 201, guest.text
    body = guest.json()["data"]
    assert body["status"] == "reserved"
    assert body["customerId"] is None
    balance = await _balance(session, product.id)
    assert Decimal(balance.reserved) == Decimal("2")


async def test_guest_checkout_rejects_unknown_or_disabled_storefronts(
    client: AsyncClient, session: Any, tenant: dict[str, Any]
) -> None:
    headers = _headers(tenant)
    await client.post(
        "/ecommerce/storefronts",
        json={"slug": "closed", "displayName": "Closed", "enabled": False},
        headers=headers,
    )
    org_slug = tenant["organization"].slug

    disabled = await client.get(f"/storefronts/{org_slug}/closed/catalogue")
    assert disabled.status_code == 404
    unknown_slug = await client.get(f"/storefronts/{org_slug}/missing/catalogue")
    assert unknown_slug.status_code == 404
    unknown_org = await client.get("/storefronts/no-such-org/closed/catalogue")
    assert unknown_org.status_code == 404

    checkout = await client.post(
        f"/storefronts/{org_slug}/closed/orders",
        json={"items": [{"storeProductId": str(uuid4()), "quantity": "1"}]},
        headers={"Idempotency-Key": ORDER_KEY + "-closed"},
    )
    assert checkout.status_code == 404


async def test_guest_checkout_requires_an_idempotency_key(
    client: AsyncClient, session: Any, tenant: dict[str, Any]
) -> None:
    headers = _headers(tenant)
    product = await _make_store_product(session, tenant, headers, client)
    made = await client.post(
        "/ecommerce/storefronts",
        json={"slug": "keyed", "displayName": "Keyed", "enabled": True},
        headers=headers,
    )
    assert made.status_code == 201, made.text

    missing = await client.post(
        f"/storefronts/{tenant['organization'].slug}/keyed/orders",
        json={"items": [{"storeProductId": str(product.id), "quantity": "1"}]},
    )
    assert missing.status_code == 422


# --- reservation expiry ------------------------------------------------------


async def test_expired_reservation_is_swept_and_strands_the_old_order(
    client: AsyncClient, session: Any, tenant: dict[str, Any]
) -> None:
    """An abandoned checkout releases its hold; completion then conflicts."""
    from datetime import timedelta as td

    from app.domains.inventory import StockReservation
    from app.security import utc_now

    headers = _headers(tenant)
    product = await _make_store_product(session, tenant, headers, client)
    await _receive(session, tenant, product.id)

    abandoned = await _checkout(
        client,
        headers,
        [{"storeProductId": str(product.id), "quantity": "3"}],
        key=ORDER_KEY + "-ttl-a",
    )
    assert abandoned.status_code == 201, abandoned.text
    abandoned_id = abandoned.json()["data"]["id"]

    reservations = list(await session.scalars(select(StockReservation)))
    assert reservations and all(r.expires_at is not None for r in reservations)
    for reservation in reservations:
        reservation.expires_at = utc_now() - td(seconds=1)
    await session.commit()

    # The next checkout sweeps the expired hold inside its own transaction.
    fresh = await _checkout(
        client,
        headers,
        [{"storeProductId": str(product.id), "quantity": "8"}],
        key=ORDER_KEY + "-ttl-b",
    )
    assert fresh.status_code == 201, fresh.text

    balance = await _balance(session, product.id)
    assert Decimal(balance.reserved) == Decimal("8")

    stranded = await client.post(
        f"/orders/{abandoned_id}/transition",
        json={"status": "completed"},
        headers=headers,
    )
    assert stranded.status_code == 409

    cancelled = await client.post(
        f"/orders/{abandoned_id}/transition",
        json={"status": "cancelled"},
        headers=headers,
    )
    assert cancelled.status_code == 200, cancelled.text
    balance = await _balance(session, product.id)
    assert Decimal(balance.reserved) == Decimal("8")


# --- listing conflicts -------------------------------------------------------


async def test_storefront_slug_reuse_across_branches_conflicts(
    client: AsyncClient, session: Any, tenant: dict[str, Any], make_store: Callable
) -> None:
    other_store = await make_store(tenant["organization"], code="SECOND")
    session.add(
        Storefront(
            organization_id=tenant["organization"].id,
            store_id=other_store.id,
            slug="shared",
            display_name="Second Branch",
            enabled=True,
        )
    )
    await session.commit()

    headers = _headers(tenant)
    clash = await client.post(
        "/ecommerce/storefronts",
        json={"slug": "shared", "displayName": "Main Branch"},
        headers=headers,
    )
    assert clash.status_code == 409, clash.text


async def test_custom_domain_clash_is_checked_on_update_too(
    client: AsyncClient,
    session: Any,
    tenant: dict[str, Any],
    make_organization: Callable,
    make_user: Callable,
    make_store: Callable,
    make_membership: Callable,
) -> None:
    other_org = await make_organization(slug="domain-org")
    other_owner = await make_user(phone="+8801700000088")
    other_store = await make_store(other_org)
    await make_membership(other_org, other_owner, Role.OWNER, other_store)
    foreign = Storefront(
        organization_id=other_org.id,
        store_id=other_store.id,
        slug="foreign",
        display_name="Foreign",
        enabled=False,
        custom_domain="taken.example.com",
    )
    session.add(foreign)
    await session.commit()

    headers = _headers(tenant)
    created = await client.post(
        "/ecommerce/storefronts",
        json={"slug": "mine", "displayName": "Mine", "enabled": True},
        headers=headers,
    )
    assert created.status_code == 201, created.text

    stolen = await client.post(
        "/ecommerce/storefronts",
        json={"slug": "mine", "displayName": "Mine", "enabled": True, "customDomain": "taken.example.com"},
        headers=headers,
    )
    assert stolen.status_code == 409, stolen.text


# --- order list shape --------------------------------------------------------


async def test_orders_list_embeds_items_and_history_in_one_response(
    client: AsyncClient, session: Any, tenant: dict[str, Any]
) -> None:
    headers = _headers(tenant)
    product = await _make_store_product(session, tenant, headers, client)
    await _receive(session, tenant, product.id, quantity="5")

    first = await _checkout(
        client, headers, [{"storeProductId": str(product.id), "quantity": "1"}], key=ORDER_KEY + "-ls-a"
    )
    second = await _checkout(
        client, headers, [{"storeProductId": str(product.id), "quantity": "2"}], key=ORDER_KEY + "-ls-b"
    )
    assert first.status_code == 201 and second.status_code == 201

    listed = await client.get("/orders", headers=headers)
    assert listed.status_code == 200, listed.text
    rows = listed.json()["data"]
    assert len(rows) == 2
    for row in rows:
        assert len(row["items"]) == 1
        assert row["items"][0]["productName"].startswith("Product ")
        assert row["history"][0]["toStatus"] == "reserved"


# --- prescriptions ----------------------------------------------------------


async def test_prescription_review_workflow_and_role_gate(
    client: AsyncClient, session: Any, tenant: dict[str, Any]
) -> None:
    from tests._phase4_helpers import role_headers

    headers = _headers(tenant)
    created = await client.post(
        "/prescriptions", json={"prescriberName": "Dr. Karim"}, headers=headers
    )
    assert created.status_code == 201, created.text
    prescription_id = created.json()["data"]["id"]

    file_added = await client.post(
        f"/prescriptions/{prescription_id}/files",
        json={"objectKey": "rx/1/img.jpg", "contentType": "image/jpeg", "checksum": "abc123"},
        headers=headers,
    )
    assert file_added.status_code == 200, file_added.text
    assert file_added.json()["data"]["files"][0]["objectKey"] == "rx/1/img.jpg"

    clarified = await client.post(
        f"/prescriptions/{prescription_id}/review",
        json={"status": "needs_clarification"},
        headers=headers,
    )
    assert clarified.status_code == 200
    rejected_twice_clarify = await client.post(
        f"/prescriptions/{prescription_id}/review",
        json={"status": "needs_clarification"},
        headers=headers,
    )
    assert rejected_twice_clarify.status_code == 409

    approved = await client.post(
        f"/prescriptions/{prescription_id}/review",
        json={"status": "approved"},
        headers=headers,
    )
    assert approved.status_code == 200
    decided = await client.post(
        f"/prescriptions/{prescription_id}/review",
        json={"status": "rejected"},
        headers=headers,
    )
    assert decided.status_code == 409

    cashier_headers = await role_headers(session, tenant, Role.CASHIER)
    denied = await client.post(
        f"/prescriptions/{prescription_id}/review",
        json={"status": "rejected"},
        headers=cashier_headers,
    )
    assert denied.status_code == 403


async def test_prescription_file_metadata_cannot_duplicate_an_object_key(
    client: AsyncClient, session: Any, tenant: dict[str, Any]
) -> None:
    headers = _headers(tenant)
    created = await client.post("/prescriptions", json={}, headers=headers)
    prescription_id = created.json()["data"]["id"]

    body = {"objectKey": "rx/dup/img.jpg", "contentType": "image/jpeg", "checksum": "abc123"}
    first = await client.post(f"/prescriptions/{prescription_id}/files", json=body, headers=headers)
    assert first.status_code == 200, first.text

    again = await client.post(f"/prescriptions/{prescription_id}/files", json=body, headers=headers)
    assert again.status_code == 409
    assert again.json()["code"] == "CONFLICT"

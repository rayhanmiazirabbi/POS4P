"""MVP coverage for the products module: pharmacy products, shelf products, price history."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
from uuid import UUID, uuid4

from app.main import app
from app.models import AuditLog, Role, StoreProduct, StoreProductPrice
from app.domains.products import PharmacyProduct
from app.domains.catalog import CatalogProduct
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession


async def _auth_session(
    session: AsyncSession, user: Any, organization: Any, store: Any = None
) -> Any:
    from datetime import timedelta

    from app.models import Session as SessionModel
    from app.security import generate_token, hash_token, utc_now

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
    return {"organization": organization, "owner": owner, "store": store, "session": auth_session}


async def _catalog_product(session: AsyncSession, **kwargs: Any) -> CatalogProduct:
    product = CatalogProduct(
        name=kwargs.pop("name", "Napa Extra"),
        package_unit=kwargs.pop("package_unit", "tablet"),
        country_code=kwargs.pop("country_code", "BD"),
        **kwargs,
    )
    session.add(product)
    await session.flush()
    return product


# --- pharmacy products ------------------------------------------------------


async def test_create_custom_pharmacy_product(
    client: Any, tenant: dict[str, Any], auth_headers: Any, session: AsyncSession
) -> None:
    response = await client.post(
        "/products",
        json={"name": "Own Saline", "unit": "piece", "barcode": "1000000001"},
        headers=auth_headers(tenant),
    )
    assert response.status_code == 201
    body = response.json()["data"]
    assert body["organizationId"] == str(tenant["organization"].id)
    assert body["name"] == "Own Saline"
    assert body["active"] is True
    assert body["catalogProductId"] is None
    action = await session.scalar(
        select(AuditLog.action).where(AuditLog.action == "product.created")
    )
    assert action == "product.created"


async def test_create_catalog_linked_product_resolves_the_catalogue_row(
    client: Any, tenant: dict[str, Any], auth_headers: Any, session: AsyncSession
) -> None:
    catalog_product = await _catalog_product(session)
    await session.commit()
    response = await client.post(
        "/products",
        json={
            "name": catalog_product.name,
            "unit": "strip",
            "catalogProductId": str(catalog_product.id),
        },
        headers=auth_headers(tenant),
    )
    assert response.status_code == 201
    assert response.json()["data"]["catalogProductId"] == str(catalog_product.id)


async def test_unknown_catalog_link_is_not_found(
    client: Any, tenant: dict[str, Any], auth_headers: Any
) -> None:
    response = await client.post(
        "/products",
        json={"name": "Ghost", "unit": "piece", "catalogProductId": str(uuid4())},
        headers=auth_headers(tenant),
    )
    assert response.status_code == 404
    assert response.json()["code"] == "NOT_FOUND"


async def test_duplicate_active_barcode_conflicts(
    client: Any, tenant: dict[str, Any], auth_headers: Any
) -> None:
    first = await client.post(
        "/products",
        json={"name": "First", "unit": "piece", "barcode": "222"},
        headers=auth_headers(tenant),
    )
    assert first.status_code == 201
    second = await client.post(
        "/products",
        json={"name": "Second", "unit": "piece", "barcode": "222"},
        headers=auth_headers(tenant),
    )
    assert second.status_code == 409
    assert second.json()["code"] == "CONFLICT"


async def test_deactivation_is_soft_and_hides_from_default_list(
    client: Any, tenant: dict[str, Any], auth_headers: Any, session: AsyncSession
) -> None:
    created = await client.post(
        "/products",
        json={"name": "Retired", "unit": "piece", "barcode": "333"},
        headers=auth_headers(tenant),
    )
    product_id = created.json()["data"]["id"]
    deactivated = await client.patch(
        f"/products/{product_id}/status",
        json={"active": False},
        headers=auth_headers(tenant),
    )
    assert deactivated.status_code == 200
    assert deactivated.json()["data"]["active"] is False

    row = await session.get(PharmacyProduct, UUID(product_id))
    assert row is not None and row.active is False, "deactivation must never hard delete"

    visible = await client.get("/products", headers=auth_headers(tenant))
    assert visible.json()["data"]["items"] == []
    with_inactive = await client.get(
        "/products", params={"includeInactive": True}, headers=auth_headers(tenant)
    )
    assert len(with_inactive.json()["data"]["items"]) == 1


async def test_cashier_cannot_create_products(
    client: Any,
    session: AsyncSession,
    tenant: dict[str, Any],
    make_user: Callable[..., Any],
    make_membership: Callable[..., Any],
) -> None:
    from tests.test_stores import _bearer

    user = await make_user(phone="+8801700000041", display_name="Cashier")
    await make_membership(tenant["organization"], user, Role.CASHIER)
    auth_session = await _auth_session(session, user, tenant["organization"])
    await session.commit()
    headers = _bearer(auth_session, user, tenant["organization"], store=tenant["store"], role=Role.CASHIER)

    response = await client.post(
        "/products", json={"name": "Nope", "unit": "piece"}, headers=headers
    )
    assert response.status_code == 403


# --- store products ---------------------------------------------------------


async def _create_pharmacy_product(client: Any, headers: dict[str, str], **fields: Any) -> str:
    payload = {"name": fields.pop("name", "Napa"), "unit": fields.pop("unit", "strip"), **fields}
    response = await client.post("/products", json=payload, headers=headers)
    assert response.status_code == 201
    return response.json()["data"]["id"]


async def _enable_store_product(
    client: Any, store_id: UUID, headers: dict[str, str], pharmacy_product_id: str, **fields: Any
) -> Any:
    payload = {
        "pharmacyProductId": pharmacy_product_id,
        "sku": fields.pop("sku", "NAPA-STRIP"),
        "salePrice": fields.pop("salePrice", "25.00"),
        **fields,
    }
    return await client.post(f"/products/stores/{store_id}", json=payload, headers=headers)


async def test_enable_store_product_and_update_price_history(
    client: Any, tenant: dict[str, Any], auth_headers: Any, session: AsyncSession
) -> None:
    product_id = await _create_pharmacy_product(client, auth_headers(tenant))
    enabled = await _enable_store_product(
        client, tenant["store"].id, auth_headers(tenant), product_id, rack="A1"
    )
    assert enabled.status_code == 201
    row = enabled.json()["data"]
    assert row["storeId"] == str(tenant["store"].id)
    assert row["salePrice"] == "25.00"
    assert row["minimumStock"] == "0"
    assert row["rack"] == "A1"
    assert row["active"] is True

    updated = await client.patch(
        f"/products/stores/{tenant['store'].id}/{row['id']}",
        json={"salePrice": "30.50", "minimumStock": "10"},
        headers=auth_headers(tenant),
    )
    assert updated.status_code == 200
    assert updated.json()["data"]["salePrice"] == "30.50"

    history = await client.get(
        f"/products/stores/{tenant['store'].id}/{row['id']}/prices",
        headers=auth_headers(tenant),
    )
    assert history.status_code == 200
    prices = [item["price"] for item in history.json()["data"]["items"]]
    assert prices == ["25.00"], "the old price must be preserved as history"

    current = await session.scalar(select(StoreProduct.sale_price))
    assert str(current) == "30.50"
    count = await session.scalar(select(func.count()).select_from(StoreProductPrice))
    assert count == 1


async def test_duplicate_sku_in_store_conflicts(
    client: Any, tenant: dict[str, Any], auth_headers: Any
) -> None:
    first = await _create_pharmacy_product(client, auth_headers(tenant), name="Alpha")
    second = await _create_pharmacy_product(client, auth_headers(tenant), name="Beta")
    assert (
        await _enable_store_product(
            client, tenant["store"].id, auth_headers(tenant), first, sku="SKU-1"
        )
    ).status_code == 201
    conflict = await _enable_store_product(
        client, tenant["store"].id, auth_headers(tenant), second, sku="SKU-1"
    )
    assert conflict.status_code == 409


async def test_cross_tenant_store_product_access_is_not_found(
    client: Any,
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
    product_id = await _create_pharmacy_product(client, auth_headers(tenant))

    foreign_enable = await _enable_store_product(
        client, other["store"].id, auth_headers(tenant), product_id
    )
    assert foreign_enable.status_code == 404
    assert foreign_enable.json()["code"] == "NOT_FOUND"

    own = await _enable_store_product(
        client, tenant["store"].id, auth_headers(tenant), product_id
    )
    row_id = own.json()["data"]["id"]
    for method, path in (
        ("patch", f"/products/stores/{other['store'].id}/{row_id}"),
        ("get", f"/products/stores/{other['store'].id}/{row_id}/prices"),
    ):
        response = await client.request(method, path, json={}, headers=auth_headers(tenant))
        assert response.status_code == 404

    rival_headers_auth_session = await _auth_session(
        session, other["owner"], other["organization"], other["store"]
    )
    await session.commit()
    from tests.test_stores import _bearer

    rival_headers = _bearer(
        rival_headers_auth_session, other["owner"], other["organization"], store=other["store"]
    )
    stolen = await client.patch(
        f"/products/stores/{other['store'].id}/{row_id}",
        json={"salePrice": "1.00"},
        headers=rival_headers,
    )
    assert stolen.status_code == 404


async def test_deactivate_store_product_keeps_rows_and_references(
    client: Any, tenant: dict[str, Any], auth_headers: Any, session: AsyncSession
) -> None:
    product_id = await _create_pharmacy_product(client, auth_headers(tenant))
    own = await _enable_store_product(
        client, tenant["store"].id, auth_headers(tenant), product_id
    )
    row_id = own.json()["data"]["id"]

    # a historical price reference exists before deactivation
    await client.patch(
        f"/products/stores/{tenant['store'].id}/{row_id}",
        json={"salePrice": "99.00"},
        headers=auth_headers(tenant),
    )

    disabled = await client.patch(
        f"/products/stores/{tenant['store'].id}/{row_id}/status",
        json={"active": False},
        headers=auth_headers(tenant),
    )
    assert disabled.status_code == 200
    assert disabled.json()["data"]["active"] is False

    row_count = await session.scalar(select(func.count()).select_from(StoreProduct))
    price_count = await session.scalar(select(func.count()).select_from(StoreProductPrice))
    assert (row_count, price_count) == (1, 1), "soft delete only"

    hidden = await client.get(
        f"/products/stores/{tenant['store'].id}", headers=auth_headers(tenant)
    )
    assert hidden.json()["data"]["items"] == []
    visible = await client.get(
        f"/products/stores/{tenant['store'].id}",
        params={"includeInactive": True},
        headers=auth_headers(tenant),
    )
    assert len(visible.json()["data"]["items"]) == 1


async def test_inventory_staff_may_read_but_not_manage(
    client: Any,
    session: AsyncSession,
    tenant: dict[str, Any],
    make_user: Callable[..., Any],
    make_membership: Callable[..., Any],
    auth_headers: Any,
) -> None:
    from tests.test_stores import _bearer

    user = await make_user(phone="+8801700000042", display_name="Inventory")
    await make_membership(tenant["organization"], user, Role.INVENTORY_STAFF, tenant["store"])
    auth_session = await _auth_session(session, user, tenant["organization"], tenant["store"])
    await session.commit()
    headers = _bearer(
        auth_session, user, tenant["organization"], store=tenant["store"], role=Role.INVENTORY_STAFF
    )

    read = await client.get("/products", headers=headers)
    assert read.status_code == 200

    denied_create = await client.post(
        "/products", json={"name": "Staff Product", "unit": "piece"}, headers=headers
    )
    assert denied_create.status_code == 403

    product_id = await _create_pharmacy_product(client, auth_headers(tenant))
    denied_enable = await _enable_store_product(
        client, tenant["store"].id, headers, product_id
    )
    assert denied_enable.status_code == 403

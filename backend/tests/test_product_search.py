"""Unified catalogue search and adoption onto the shop shelf."""

from __future__ import annotations

from decimal import Decimal
from typing import Any
from uuid import uuid4

from sqlalchemy import select

from app.domains.catalog import CatalogBarcode
from app.domains.products import PharmacyProduct, StoreProduct
from app.models import AuditLog, Role
from tests.test_products import _auth_session, _catalog_product


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


async def _shelf_row(
    session: Any, tenant: dict[str, Any], product: PharmacyProduct, *, sku: str
) -> StoreProduct:
    row = StoreProduct(
        organization_id=tenant["organization"].id,
        store_id=tenant["store"].id,
        pharmacy_product_id=product.id,
        sku=sku,
        sale_price=Decimal("12.50"),
        minimum_stock=Decimal(0),
        active=True,
    )
    session.add(row)
    await session.flush()
    return row


# --- unified search ---------------------------------------------------------------


async def test_search_merges_catalog_custom_and_shop_status(
    client: Any, session: Any, tenant: dict[str, Any]
) -> None:
    catalog_on_shelf = await _catalog_product(session, name="Napa Extra")
    await _catalog_product(session, name="Napa Syrup")
    linked_org = PharmacyProduct(
        organization_id=tenant["organization"].id,
        catalog_product_id=catalog_on_shelf.id,
        name="Napa Extra",
        unit="tablet",
        active=True,
    )
    session.add(linked_org)
    await session.flush()
    await _shelf_row(session, tenant, linked_org, sku="NAPA-500")
    custom_in_org = PharmacyProduct(
        organization_id=tenant["organization"].id,
        name="Napa Own Mix",
        unit="bottle",
        active=True,
    )
    session.add(custom_in_org)
    await session.commit()

    page = (await client.get("/products/search?q=napa", headers=_headers(tenant))).json()["data"]
    rows = {row["name"]: row for row in page["items"]}

    shelf_row = rows["Napa Extra"]
    assert shelf_row["kind"] == "catalog"
    assert shelf_row["shopStatus"] == "on_shelf"
    assert shelf_row["storeProductId"] is not None
    assert shelf_row["salePrice"] == "12.50"
    assert shelf_row["sku"] == "NAPA-500"

    absent_row = rows["Napa Syrup"]
    assert absent_row["kind"] == "catalog"
    assert absent_row["shopStatus"] == "absent"
    assert absent_row["pharmacyProductId"] is None

    custom_row = rows["Napa Own Mix"]
    assert custom_row["kind"] == "custom"
    assert custom_row["shopStatus"] in ("in_org", "on_shelf")
    assert custom_row["referenceUnitPrice"] is None
    assert custom_row["packageUnit"] == "bottle"


async def test_linked_product_renders_once_even_when_only_catalog_matches(
    client: Any, session: Any, tenant: dict[str, Any]
) -> None:
    """Dedupe: the org alias must not produce a second row beside its catalogue entry."""
    catalog = await _catalog_product(session, name="Seclo 20")
    org_product = PharmacyProduct(
        organization_id=tenant["organization"].id,
        catalog_product_id=catalog.id,
        # A local label that does not contain the search term at all.
        name="Local Stomach Pill",
        unit="capsule",
        active=True,
    )
    session.add(org_product)
    await session.commit()

    page = (await client.get("/products/search?q=seclo", headers=_headers(tenant))).json()["data"]
    assert page["total"] == 1
    row = page["items"][0]
    assert row["kind"] == "catalog"
    assert row["shopStatus"] == "in_org"
    assert row["pharmacyProductId"] == str(org_product.id)


async def test_exact_barcode_ranks_first_and_matches_generic_name(
    client: Any, session: Any, tenant: dict[str, Any]
) -> None:
    await _catalog_product(session, name="Aaa Tablet")
    barcode_hit = await _catalog_product(session, name="Zzz Tablet", strength="40mg")
    generic_hit = await _catalog_product(session, name="Brand X")
    generic_hit.generic_name = "Omeprazole"
    session.add(generic_hit)
    session.add(CatalogBarcode(catalog_product_id=barcode_hit.id, barcode="8809999"))
    await session.commit()

    by_barcode = (
        await client.get("/products/search?q=8809999", headers=_headers(tenant))
    ).json()["data"]
    assert [row["name"] for row in by_barcode["items"]] == ["Zzz Tablet"]

    both = (await client.get("/products/search?q=tablet", headers=_headers(tenant))).json()["data"]
    names = [row["name"] for row in both["items"]]
    assert set(names) >= {"Aaa Tablet", "Zzz Tablet"}
    assert names[0] == "Aaa Tablet"

    by_generic = (
        await client.get("/products/search?q=omepraz", headers=_headers(tenant))
    ).json()["data"]
    assert [row["name"] for row in by_generic["items"]] == ["Brand X"]
    assert by_generic["items"][0]["genericName"] == "Omeprazole"


# --- adoption ----------------------------------------------------------------------


async def test_adopt_creates_product_and_shelf_row_with_reference_price(
    client: Any, session: Any, tenant: dict[str, Any]
) -> None:
    catalog = await _catalog_product(
        session, name="Adopt Price 500mg", strength="500mg", unit_price=Decimal("8.00")
    )
    session.add(CatalogBarcode(catalog_product_id=catalog.id, barcode="8801234567890"))
    await session.commit()

    response = await client.post(
        "/products/adopt",
        json={"catalogProductId": str(catalog.id)},
        headers=_headers(tenant),
    )
    assert response.status_code == 201, response.text
    data = response.json()["data"]
    assert data["pharmacyProduct"]["name"] == "Adopt Price 500mg"
    assert data["pharmacyProduct"]["barcode"] == "8801234567890"
    assert data["storeProduct"]["salePrice"] == "8.00"
    assert data["storeProduct"]["sku"]  # deterministic SKU was generated

    action = await session.scalar(select(AuditLog.action).where(AuditLog.action == "product.adopted"))
    assert action == "product.adopted"


async def test_explicit_sale_price_wins_over_reference(
    client: Any, session: Any, tenant: dict[str, Any]
) -> None:
    catalog = await _catalog_product(session, name="Priced Two Ways", unit_price=Decimal("5.00"))
    await session.commit()
    response = await client.post(
        "/products/adopt",
        json={"catalogProductId": str(catalog.id), "salePrice": "7.25"},
        headers=_headers(tenant),
    )
    assert response.json()["data"]["storeProduct"]["salePrice"] == "7.25"


async def test_adopt_without_any_price_is_rejected(
    client: Any, session: Any, tenant: dict[str, Any]
) -> None:
    catalog = await _catalog_product(session, name="No Price Entry")
    await session.commit()
    response = await client.post(
        "/products/adopt",
        json={"catalogProductId": str(catalog.id)},
        headers=_headers(tenant),
    )
    assert response.status_code == 422


async def test_second_adopt_reuses_then_reactivates(
    client: Any, session: Any, tenant: dict[str, Any]
) -> None:
    catalog = await _catalog_product(session, name="Readopt 250mg", unit_price=Decimal("3.00"))
    await session.commit()
    first = await client.post(
        "/products/adopt",
        json={"catalogProductId": str(catalog.id)},
        headers=_headers(tenant),
    )
    pharmacy_id = first.json()["data"]["pharmacyProduct"]["id"]
    store_product_id = first.json()["data"]["storeProduct"]["id"]

    deactivated = await client.patch(
        f"/products/{pharmacy_id}/status",
        json={"active": False},
        headers=_headers(tenant),
    )
    assert deactivated.status_code == 200

    second = await client.post(
        "/products/adopt",
        json={"catalogProductId": str(catalog.id), "salePrice": "4.00"},
        headers=_headers(tenant),
    )
    assert second.status_code == 201
    data = second.json()["data"]
    assert data["pharmacyProduct"]["id"] == pharmacy_id
    assert data["storeProduct"]["id"] == store_product_id
    assert data["pharmacyProduct"]["active"] is True
    assert data["storeProduct"]["active"] is True

    products = list(await session.scalars(select(PharmacyProduct)))
    assert len([p for p in products if p.name == "Readopt 250mg"]) == 1


async def test_cashier_cannot_adopt(
    client: Any,
    session: Any,
    tenant: dict[str, Any],
    make_user: Any,
    make_membership: Any,
) -> None:
    from tests.test_catalog import _bearer

    catalog = await _catalog_product(session, name="Cashier Blocked", unit_price=Decimal("1.00"))
    cashier = await make_user(phone="+8801700000044", display_name="Cashier Adopt")
    await make_membership(tenant["organization"], cashier, Role.CASHIER, tenant["store"])
    auth_session = await _auth_session(session, cashier, tenant["organization"], tenant["store"])
    await session.commit()
    headers = _bearer(auth_session, cashier, tenant["organization"], store=tenant["store"], role=Role.CASHIER)

    response = await client.post(
        "/products/adopt",
        json={"catalogProductId": str(catalog.id), "salePrice": "2.00"},
        headers=headers,
    )
    assert response.status_code == 403
    assert response.json()["code"] == "FORBIDDEN"


async def test_unknown_or_inactive_catalog_entry_not_found(
    client: Any, session: Any, tenant: dict[str, Any]
) -> None:
    missing = await client.post(
        "/products/adopt",
        json={"catalogProductId": str(uuid4()), "salePrice": "1.00"},
        headers=_headers(tenant),
    )
    assert missing.status_code == 404

    inactive = await _catalog_product(session, name="Retired Drug", active=False)
    await session.commit()
    response = await client.post(
        "/products/adopt",
        json={"catalogProductId": str(inactive.id), "salePrice": "1.00"},
        headers=_headers(tenant),
    )
    assert response.status_code == 404

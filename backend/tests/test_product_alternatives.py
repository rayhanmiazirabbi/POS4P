"""Alternatives: other brands of one generic, this shop's status on each."""

from __future__ import annotations

from decimal import Decimal
from typing import Any
from uuid import uuid4

from app.domains.catalog import DosageForm, Manufacturer
from app.domains.products import PharmacyProduct
from app.models import Role
from tests.test_products import _catalog_product
from tests.test_product_search import _headers, _shelf_row


async def _generic(session: Any, generic: str, **kwargs: Any) -> Any:
    """A catalogue row whose generic is the whole point of the test."""
    return await _catalog_product(session, generic_name=generic, **kwargs)


async def test_alternatives_list_other_brands_of_the_same_generic(
    client: Any, session: Any, tenant: dict[str, Any]
) -> None:
    target = await _generic(session, "Paracetamol", name="Napa 500")
    await _generic(session, "Paracetamol", name="Ace 500")
    await _generic(session, "Paracetamol + Caffeine", name="Napa Extra")  # combination: not the same medicine
    await _generic(session, "Ibuprofen", name="Brufen 400")
    await _generic(session, "Paracetamol", name="Retired Brand", active=False)
    await session.commit()

    page = (
        await client.get(
            "/products/alternatives",
            params={"genericName": "Paracetamol", "excludeCatalogProductId": str(target.id)},
            headers=_headers(tenant),
        )
    ).json()["data"]

    assert [row["name"] for row in page["items"]] == ["Ace 500"]
    assert page["total"] == 1
    row = page["items"][0]
    assert row["genericName"] == "Paracetamol"
    assert row["shopStatus"] == "absent"
    assert row["pharmacyProductId"] is None


async def test_alternatives_report_shop_status_and_tiers(
    client: Any, session: Any, tenant: dict[str, Any]
) -> None:
    manufacturer = Manufacturer(name="Square Pharma")
    session.add(manufacturer)
    tablet = DosageForm(name="Tablet")
    session.add(tablet)
    target = await _generic(
        session, "Paracetamol", name="Napa 500", strength="500 mg", dosage_form_id=None
    )
    on_shelf = await _generic(
        session, "Paracetamol", name="Ace 500", strength="500mg", dosage_form_id=tablet.id
    )
    in_org = await _generic(
        session, "Paracetamol", name="Bez 500", strength="500 mg", manufacturer_id=manufacturer.id
    )
    elsewhere = await _generic(session, "Paracetamol", name="Cee 650", strength="650 mg")
    await _generic(session, "Paracetamol", name="Dee Syrup", strength="500 mg")
    await session.flush()

    linked = PharmacyProduct(
        organization_id=tenant["organization"].id,
        catalog_product_id=in_org.id,
        name="Bez 500",
        unit="tablet",
        active=True,
    )
    session.add(linked)
    await session.flush()
    on_shelf_org = PharmacyProduct(
        organization_id=tenant["organization"].id,
        catalog_product_id=on_shelf.id,
        name="Ace 500",
        unit="tablet",
        active=True,
    )
    session.add(on_shelf_org)
    await session.flush()
    shelf = await _shelf_row(session, tenant, on_shelf_org, sku="ACE-500")
    await session.commit()

    page = (
        await client.get(
            "/products/alternatives",
            params={
                "genericName": "paracetamol",
                "excludeCatalogProductId": str(target.id),
                "strength": "500 mg",
                "dosageFormId": str(tablet.id),
            },
            headers=_headers(tenant),
        )
    ).json()["data"]

    rows = {row["name"]: row for row in page["items"]}
    # Tier 0 (same strength + form), tier 1 (same strength), tier 2 (other strength).
    assert [row["name"] for row in page["items"]] == ["Ace 500", "Bez 500", "Dee Syrup", "Cee 650"]

    ace = rows["Ace 500"]
    assert ace["shopStatus"] == "on_shelf"
    assert ace["storeProductId"] == str(shelf.id)
    assert ace["sku"] == "ACE-500"
    assert ace["salePrice"] == "12.50"
    assert ace["sameStrength"] is True
    assert ace["sameDosageForm"] is True

    bez = rows["Bez 500"]
    assert bez["shopStatus"] == "in_org"
    assert bez["pharmacyProductId"] == str(linked.id)
    assert bez["storeProductId"] is None
    assert bez["sameStrength"] is True
    assert bez["sameDosageForm"] is False
    assert bez["manufacturer"] == "Square Pharma"

    dee = rows["Dee Syrup"]
    assert dee["sameStrength"] is True
    assert dee["sameDosageForm"] is False

    cee = rows["Cee 650"]
    assert cee["shopStatus"] == "absent"
    assert cee["sameStrength"] is False


async def test_alternatives_paginate(client: Any, session: Any, tenant: dict[str, Any]) -> None:
    await _generic(session, "Amoxicillin", name="Moxa 500")
    await _generic(session, "Amoxicillin", name="Moxb 500")
    await _generic(session, "Amoxicillin", name="Moxc 500")
    await session.commit()

    first = (
        await client.get(
            "/products/alternatives",
            params={"genericName": "Amoxicillin", "limit": 2},
            headers=_headers(tenant),
        )
    ).json()["data"]
    assert first["total"] == 3
    assert [row["name"] for row in first["items"]] == ["Moxa 500", "Moxb 500"]

    second = (
        await client.get(
            "/products/alternatives",
            params={"genericName": "Amoxicillin", "limit": 2, "offset": 2},
            headers=_headers(tenant),
        )
    ).json()["data"]
    assert [row["name"] for row in second["items"]] == ["Moxc 500"]


async def test_alternatives_rejects_a_blank_generic(
    client: Any, session: Any, tenant: dict[str, Any]
) -> None:
    missing = await client.get("/products/alternatives", headers=_headers(tenant))
    assert missing.status_code == 422

    blank = await client.get(
        "/products/alternatives", params={"genericName": "   "}, headers=_headers(tenant)
    )
    assert blank.status_code == 422


async def test_alternatives_route_is_not_swallowed_by_the_uuid_route(
    client: Any, session: Any, tenant: dict[str, Any]
) -> None:
    """`/alternatives` must answer as itself, not be parsed as a `{product_id}`.

    The literal-vs-parameter ordering bug is documented on ``/current``: a route
    declared after the UUID path is unreachable, and the last failure mode was
    an endpoint every counter loads answering 422 to every call. This test
    exists so the next route added above it cannot reintroduce that silently.
    """
    response = await client.get(
        "/products/alternatives", params={"genericName": "x"}, headers=_headers(tenant)
    )
    assert response.status_code == 200
    assert response.json()["data"]["total"] == 0


async def test_cashier_can_read_alternatives(
    client: Any,
    session: Any,
    tenant: dict[str, Any],
    make_user: Any,
    make_membership: Any,
) -> None:
    """Alternatives are a counter question, not a management one."""
    from tests.test_products import _auth_session
    from tests.test_catalog import _bearer

    await _generic(session, " Cetirizine ", name="Zyncet 10")
    await session.commit()
    cashier = await make_user(phone="+8801700000055", display_name="Cashier Alt")
    await make_membership(tenant["organization"], cashier, Role.CASHIER, tenant["store"])
    auth_session = await _auth_session(session, cashier, tenant["organization"], tenant["store"])
    await session.commit()
    headers = _bearer(
        auth_session, cashier, tenant["organization"], store=tenant["store"], role=Role.CASHIER
    )

    response = await client.get(
        "/products/alternatives", params={"genericName": "Cetirizine"}, headers=headers
    )
    assert response.status_code == 200
    # Normalization is shared with the client: spacing and case fold to equality.
    assert [row["name"] for row in response.json()["data"]["items"]] == ["Zyncet 10"]

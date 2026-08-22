from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta
from typing import Any
from uuid import UUID

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.catalog import CatalogRevision
from app.main import app
from app.models import Role
from app.models import Session as SessionModel
from app.security import generate_token, hash_token, sign_access_token, utc_now


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


def _product_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "name": "Napa Extra",
        "packageSize": "10",
        "packageUnit": "tablet",
        "countryCode": "BD",
        "prescriptionRequired": False,
    }
    payload.update(overrides)
    return payload


async def test_manufacturer_crud_and_duplicate(
    client: AsyncClient, tenant: dict[str, Any], auth_headers: Any
) -> None:
    headers = auth_headers(tenant)
    created = await client.post(
        "/catalog/manufacturers", json={"name": "Beximco", "countryCode": "BD"}, headers=headers
    )
    assert created.status_code == 201
    body = created.json()["data"]
    assert body["name"] == "Beximco"
    assert body["countryCode"] == "BD"
    assert body["active"] is True

    duplicate = await client.post(
        "/catalog/manufacturers", json={"name": "Beximco"}, headers=headers
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["code"] == "CONFLICT"

    updated = await client.patch(
        f"/catalog/manufacturers/{body['id']}", json={"active": False}, headers=headers
    )
    assert updated.status_code == 200
    assert updated.json()["data"]["active"] is False

    listing = await client.get("/catalog/manufacturers", headers=headers)
    assert listing.status_code == 200
    assert [row["id"] for row in listing.json()["data"]["items"]] == [body["id"]]


async def test_ingredient_and_dosage_form_crud_with_duplicates(
    client: AsyncClient, tenant: dict[str, Any], auth_headers: Any
) -> None:
    headers = auth_headers(tenant)
    ingredient = await client.post(
        "/catalog/ingredients", json={"name": "Paracetamol"}, headers=headers
    )
    assert ingredient.status_code == 201
    assert (await client.post("/catalog/ingredients", json={"name": "Paracetamol"}, headers=headers)).status_code == 409

    form = await client.post("/catalog/dosage-forms", json={"name": "Tablet"}, headers=headers)
    assert form.status_code == 201
    assert (await client.post("/catalog/dosage-forms", json={"name": "Tablet"}, headers=headers)).status_code == 409


async def test_product_create_with_children_and_revisions(
    client: AsyncClient,
    session: AsyncSession,
    tenant: dict[str, Any],
    auth_headers: Any,
) -> None:
    headers = auth_headers(tenant)
    manufacturer = (
        await client.post("/catalog/manufacturers", json={"name": "Square"}, headers=headers)
    ).json()["data"]
    form = (
        await client.post("/catalog/dosage-forms", json={"name": "Tablet"}, headers=headers)
    ).json()["data"]
    ingredient = (
        await client.post("/catalog/ingredients", json={"name": "Paracetamol"}, headers=headers)
    ).json()["data"]

    created = await client.post(
        "/catalog/products",
        json=_product_payload(
            manufacturerId=manufacturer["id"],
            dosageFormId=form["id"],
            strength="500mg",
            ingredients=[{"activeIngredientId": ingredient["id"], "strength": "500", "unit": "mg"}],
            barcodes=[{"barcode": "8801234567890"}],
            aliases=[{"alias": "napa extra 500"}],
        ),
        headers=headers,
    )
    assert created.status_code == 201, created.text
    product = created.json()["data"]
    assert product["barcodes"] == ["8801234567890"]
    assert product["aliases"] == ["napa extra 500"]
    assert product["ingredients"][0]["activeIngredientId"] == ingredient["id"]

    revisions = await client.get(
        f"/catalog/products/{product['id']}/revisions", headers=headers
    )
    assert revisions.status_code == 200
    rows = revisions.json()["data"]
    assert len(rows) == 1
    assert rows[0]["revision"] == 1
    assert rows[0]["changedByUserId"] == str(tenant["owner"].id)
    assert rows[0]["data"]["name"] == "Napa Extra"


async def test_duplicate_barcode_conflicts(
    client: AsyncClient,
    session: AsyncSession,
    tenant: dict[str, Any],
    auth_headers: Any,
) -> None:
    headers = auth_headers(tenant)
    first = await client.post(
        "/catalog/products",
        json=_product_payload(barcodes=[{"barcode": "DUP-0001"}]),
        headers=headers,
    )
    assert first.status_code == 201
    second = await client.post(
        "/catalog/products",
        json=_product_payload(name="Other Drug", barcodes=[{"barcode": "DUP-0001"}]),
        headers=headers,
    )
    assert second.status_code == 409
    assert second.json()["code"] == "CONFLICT"


async def test_revisions_increment_on_update_and_are_append_only(
    client: AsyncClient,
    session: AsyncSession,
    tenant: dict[str, Any],
    auth_headers: Any,
) -> None:
    headers = auth_headers(tenant)
    product_id = (
        await client.post("/catalog/products", json=_product_payload(), headers=headers)
    ).json()["data"]["id"]

    await client.patch(
        f"/catalog/products/{product_id}", json={"strength": "665mg"}, headers=headers
    )
    await client.patch(
        f"/catalog/products/{product_id}", json={"prescriptionRequired": True}, headers=headers
    )

    revisions = (
        await client.get(f"/catalog/products/{product_id}/revisions", headers=headers)
    ).json()["data"]
    assert [row["revision"] for row in revisions] == [1, 2, 3]
    assert revisions[2]["data"]["strength"] == "665mg"
    assert revisions[2]["data"]["prescriptionRequired"] is True

    stored = list(
        await session.scalars(
            select(CatalogRevision).where(
                CatalogRevision.catalog_product_id == UUID(product_id)
            )
        )
    )
    assert len(stored) == 3
    assert len({row.id for row in stored}) == 3


async def test_search_by_name_alias_barcode_and_country_filter(
    client: AsyncClient,
    session: AsyncSession,
    tenant: dict[str, Any],
    auth_headers: Any,
) -> None:
    headers = auth_headers(tenant)
    by_name = await client.post(
        "/catalog/products", json=_product_payload(), headers=headers
    )
    by_alias = await client.post(
        "/catalog/products",
        json=_product_payload(name="Ace", aliases=[{"alias": "ace junior syrup"}], countryCode="IN"),
        headers=headers,
    )
    by_barcode = await client.post(
        "/catalog/products",
        json=_product_payload(name="Seclo", barcodes=[{"barcode": "SECLO-BARCODE"}]),
        headers=headers,
    )
    assert all(response.status_code == 201 for response in (by_name, by_alias, by_barcode))

    name_hits = (await client.get("/catalog/products?q=napa%20ext", headers=headers)).json()["data"]
    assert [item["id"] for item in name_hits["items"]] == [by_name.json()["data"]["id"]]

    alias_hits = (await client.get("/catalog/products?q=junior", headers=headers)).json()["data"]
    assert [item["id"] for item in alias_hits["items"]] == [by_alias.json()["data"]["id"]]

    barcode_hits = (await client.get("/catalog/products?q=SECLO-BARCODE", headers=headers)).json()["data"]
    assert [item["id"] for item in barcode_hits["items"]] == [by_barcode.json()["data"]["id"]]

    bd_only = (await client.get("/catalog/products?countryCode=BD", headers=headers)).json()["data"]
    ids = {item["id"] for item in bd_only["items"]}
    assert by_name.json()["data"]["id"] in ids
    assert by_alias.json()["data"]["id"] not in ids

    rx_only = (
        await client.get("/catalog/products?prescriptionRequired=true", headers=headers)
    ).json()["data"]
    assert rx_only["total"] == 0


async def test_non_manager_cannot_write_but_can_read(
    client: AsyncClient,
    session: AsyncSession,
    tenant: dict[str, Any],
    auth_headers: Any,
    make_user: Callable[..., Any],
    make_membership: Callable[..., Any],
) -> None:
    owner_headers = auth_headers(tenant)
    product = (
        await client.post("/catalog/products", json=_product_payload(), headers=owner_headers)
    ).json()["data"]

    cashier = await make_user(phone="+8801700000031", display_name="Cashier")
    await make_membership(tenant["organization"], cashier, Role.CASHIER, tenant["store"])
    cashier_auth = await _auth_session(session, cashier, tenant["organization"], tenant["store"])
    await session.commit()
    cashier_headers = _bearer(cashier_auth, cashier, tenant["organization"], store=tenant["store"], role=Role.CASHIER)

    forbidden = [
        ("post", "/catalog/manufacturers", {"name": "Nope"}),
        ("post", "/catalog/ingredients", {"name": "Nope"}),
        ("post", "/catalog/dosage-forms", {"name": "Nope"}),
        ("post", "/catalog/products", _product_payload()),
        ("patch", f"/catalog/products/{product['id']}", {"strength": "1mg"}),
    ]
    for method, path, payload in forbidden:
        response = await client.request(method, path, json=payload, headers=cashier_headers)
        assert response.status_code == 403, (method, path)
        assert response.json()["code"] == "FORBIDDEN"

    readable = await client.get("/catalog/products", headers=cashier_headers)
    assert readable.status_code == 200
    assert readable.json()["data"]["total"] == 1


async def test_any_tenant_reads_shared_catalog(
    client: AsyncClient,
    session: AsyncSession,
    tenant: dict[str, Any],
    auth_headers: Any,
    make_organization: Callable[..., Any],
    make_user: Callable[..., Any],
    make_store: Callable[..., Any],
    make_membership: Callable[..., Any],
) -> None:
    """Catalogue rows carry no tenant; writes stay role-gated, reads stay shared."""
    headers = auth_headers(tenant)
    created = await client.post("/catalog/manufacturers", json={"name": "Shared Co"}, headers=headers)
    assert created.status_code == 201

    rival_org = await make_organization(name="Rival", slug="rival-catalog")
    rival_user = await make_user(phone="+8801788888888", display_name="Rival")
    await make_membership(rival_org, rival_user, Role.OWNER)
    rival_auth = await _auth_session(session, rival_user, rival_org)
    await session.commit()
    rival_headers = _bearer(rival_auth, rival_user, rival_org)

    listing = await client.get("/catalog/manufacturers", headers=rival_headers)
    assert listing.status_code == 200
    assert [row["name"] for row in listing.json()["data"]["items"]] == ["Shared Co"]

    write_attempt = await client.post(
        "/catalog/manufacturers", json={"name": "Rival Only"}, headers=rival_headers
    )
    assert write_attempt.status_code == 201

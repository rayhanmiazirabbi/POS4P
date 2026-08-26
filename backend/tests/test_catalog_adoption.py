"""Catalogue reference-price and generic-name fields, plus the migration backfill."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.catalog import (
    ActiveIngredient,
    CatalogProduct,
    CatalogProductIngredient,
)
from tests.test_catalog import _product_payload


def _backfill_sql() -> str:
    """Load the 0011 migration module by path; its name starts with a digit."""
    path = (
        Path(__file__).parents[1]
        / "migrations"
        / "versions"
        / "0011_catalog_adoption_purchase_orders.py"
    )
    spec = importlib.util.spec_from_file_location("migration_0011", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module._BACKFILL_GENERIC_NAME


async def test_product_round_trips_new_fields(
    client: Any, tenant: dict[str, Any], auth_headers: Any
) -> None:
    headers = auth_headers(tenant)
    created = await client.post(
        "/catalog/products",
        json=_product_payload(
            genericName="Paracetamol",
            unitPrice="8.50",
            stripPrice="85.00",
        ),
        headers=headers,
    )
    assert created.status_code == 201, created.text
    product = created.json()["data"]
    assert product["genericName"] == "Paracetamol"
    assert product["unitPrice"] == "8.50"
    assert product["stripPrice"] == "85.00"

    fetched = await client.get(f"/catalog/products/{product['id']}", headers=headers)
    assert fetched.json()["data"]["unitPrice"] == "8.50"

    updated = await client.patch(
        f"/catalog/products/{product['id']}",
        json={"stripPrice": "90.00"},
        headers=headers,
    )
    assert updated.status_code == 200
    assert updated.json()["data"]["stripPrice"] == "90.00"

    revisions = await client.get(f"/catalog/products/{product['id']}/revisions", headers=headers)
    snapshot = revisions.json()["data"][0]["data"]
    assert snapshot["genericName"] == "Paracetamol"
    assert snapshot["unitPrice"] == "8.50"


async def test_catalog_search_matches_generic_name(
    client: Any, session: AsyncSession, tenant: dict[str, Any], auth_headers: Any
) -> None:
    product = CatalogProduct(
        name="Mystery Brand", generic_name="Cetirizine", package_unit="tablet", country_code="BD"
    )
    session.add(product)
    await session.commit()
    hits = await client.get("/catalog/products?q=cetiriz", headers=auth_headers(tenant))
    assert [row["id"] for row in hits.json()["data"]["items"]] == [str(product.id)]


async def test_backfill_copies_exactly_one_ingredient(session: AsyncSession) -> None:
    single = CatalogProduct(name="Solo Drug", package_unit="tablet", country_code="BD")
    combo = CatalogProduct(name="Combo Drug", package_unit="tablet", country_code="BD")
    ingredient_a = ActiveIngredient(name="Backfill Ingredient A")
    ingredient_b = ActiveIngredient(name="Backfill Ingredient B")
    session.add_all([single, combo, ingredient_a, ingredient_b])
    await session.flush()
    session.add_all(
        [
            CatalogProductIngredient(catalog_product_id=single.id, active_ingredient_id=ingredient_a.id),
            CatalogProductIngredient(catalog_product_id=combo.id, active_ingredient_id=ingredient_a.id),
            CatalogProductIngredient(catalog_product_id=combo.id, active_ingredient_id=ingredient_b.id),
        ]
    )
    await session.commit()

    await session.execute(text(_backfill_sql()))
    await session.refresh(single)
    await session.refresh(combo)

    assert single.generic_name == "Backfill Ingredient A"
    # A combination product has no single generic; it must stay NULL.
    assert combo.generic_name is None

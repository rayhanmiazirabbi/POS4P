"""Typo-tolerant medicine search: scorer golden vectors and /products/search ranking."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from app.domains.catalog import CatalogAlias, CatalogBarcode, DosageForm, Manufacturer
from app.domains.products import PharmacyProduct, StoreProduct
from app.services.medicine_search import (
    medicine_edit_distance,
    match_medicine_text,
    normalize_medicine_text,
)
from tests.test_product_search import _headers, _shelf_row
from tests.test_products import _catalog_product


# --- scorer golden vectors ---------------------------------------------------------
#
# The same rows live in `packages/sync/tests/medicineSearch.test.ts`. When one
# changes here it changes there: the server and the three counter shells rank
# typos through one contract, and these vectors are what keeps them identical.


async def _search(client: Any, tenant: dict[str, Any], query: str, **params: Any) -> dict[str, Any]:
    response = await client.get(
        "/products/search", params={"q": query, **params}, headers=_headers(tenant)
    )
    assert response.status_code == 200, response.text
    return response.json()["data"]


def test_normalization_folds_case_whitespace_and_strength_spacing() -> None:
    assert normalize_medicine_text("  NAPA   Extra ") == normalize_medicine_text("napa extra")
    assert normalize_medicine_text("Napa 500 mg") == normalize_medicine_text("napa 500mg")
    assert normalize_medicine_text("Alpha‑D3") == "alpha-d3"


def test_edit_distance_counts_substitution_transposition_and_omission() -> None:
    assert medicine_edit_distance("omeprazle", "omeprazole") == 1
    assert medicine_edit_distance("paracetmaol", "paracetamol") == 1
    assert medicine_edit_distance("npa", "napa") == 1


def test_scorer_golden_vectors() -> None:
    """Mirror of the TypeScript golden table -- see the module docstring."""
    vectors: list[tuple[str, dict[str, Any], tuple[str, str] | None]] = [
        ("Napa Extra", {"name": "Napa Extra"}, ("name", "exact")),
        ("  NAPA   extra ", {"name": "Napa Extra"}, ("name", "exact")),
        ("paracetamol", {"name": "Napa 500", "generic_name": "Paracetamol"}, ("genericName", "exact")),
        ("napa", {"name": "Napa Extra"}, ("name", "partial")),
        ("paracet", {"name": "Napa 500", "generic_name": "Paracetamol"}, ("genericName", "partial")),
        ("npa", {"name": "Napa"}, ("name", "fuzzy")),
        ("omeprazle", {"name": "Omeprazole"}, ("name", "fuzzy")),
        ("paracetmaol", {"name": "Paracetamol", "generic_name": "Paracetamol"}, ("name", "fuzzy")),
        ("paracetamal", {"name": "Napa 500", "generic_name": "Paracetamol"}, ("genericName", "fuzzy")),
        ("yx", {"name": "Napa"}, None),
        ("na", {"name": "Napa"}, ("name", "partial")),
        ("naproxen", {"name": "Napa"}, None),
        ("napa 500 tablet", {"name": "Napa", "strength": "500 mg", "dosage_form": "Tablet"}, ("name", "exact")),
        ("napa 500mg", {"name": "Napa", "strength": "500 mg", "dosage_form": "Tablet"}, ("name", "exact")),
        ("nurofen 200", {"name": "Nurofen 200"}, ("name", "exact")),
        ("napa 500", {"name": "Napa", "strength": "650 mg"}, None),
        ("napa 650", {"name": "Napa", "strength": "500 mg"}, None),
        ("napa syrup", {"name": "Napa", "dosage_form": "Tablet"}, None),
        ("500 mg", {"name": "Napa", "strength": "500 mg"}, ("strength", "supporting")),
        ("tablet", {"name": "Napa", "dosage_form": "Tablet"}, ("dosageForm", "supporting")),
        (
            "chlorpheniramine oral solution",
            {"name": "Chlorpheniramine", "dosage_form": "Oral Solution"},
            ("name", "exact"),
        ),
        ("honey cough mix", {"name": "Honey Cough Mix"}, ("name", "exact")),
    ]
    for query, item, expected in vectors:
        match = match_medicine_text(
            raw_query=query,
            name=item.get("name", ""),
            generic_name=item.get("generic_name"),
            strength=item.get("strength"),
            dosage_form=item.get("dosage_form"),
        )
        if expected is None:
            assert match is None, (query, item, match)
            continue
        assert match is not None, (query, item)
        assert (match.field, match.quality) == expected, (query, item, match)


def test_fuzzy_score_of_the_canonical_omission() -> None:
    match = match_medicine_text(name="Napa", generic_name=None, strength=None, dosage_form=None, raw_query="npa")
    assert match is not None
    assert match.score == 0.75


# --- /products/search ranking ------------------------------------------------------


async def _form(session: Any, name: str) -> DosageForm:
    row = DosageForm(name=name)
    session.add(row)
    await session.flush()
    return row


async def _maker(session: Any, name: str) -> Manufacturer:
    row = Manufacturer(name=name)
    session.add(row)
    await session.flush()
    return row


async def test_ranking_tiers_order_and_metadata(
    client: Any, session: Any, tenant: dict[str, Any]
) -> None:
    """Barcode < SKU < brand < generic < alias < partial < fuzzy, with metadata on every row."""
    tablet = await _form(session, "Tablet")
    beximco = await _maker(session, "Tier Beximco")
    square = await _maker(session, "Tier Square")

    barcode_hit = await _catalog_product(session, name="Zzz Scanned", strength="10mg")
    session.add(CatalogBarcode(catalog_product_id=barcode_hit.id, barcode="8801"))

    sku_product = PharmacyProduct(
        organization_id=tenant["organization"].id,
        name="Aaa Typed Sku",
        unit="tablet",
        active=True,
    )
    session.add(sku_product)
    await session.flush()
    await _shelf_row(session, tenant, sku_product, sku="MYSKU-1")

    brand_hit = await _catalog_product(
        session, name="Napa", generic_name="Paracetamol", strength="500 mg",
        dosage_form_id=tablet.id, manufacturer_id=beximco.id,
    )
    generic_hit = await _catalog_product(
        session, name="Miserably Named", generic_name="Paracetamol",
        dosage_form_id=tablet.id, manufacturer_id=square.id,
    )
    alias_hit = await _catalog_product(session, name="Seclo")
    session.add(CatalogAlias(catalog_product_id=alias_hit.id, alias="Socid"))
    fuzzy_hit = await _catalog_product(session, name="Omeprazole")
    await session.commit()

    by_barcode = await _search(client, tenant, "8801")
    assert [row["name"] for row in by_barcode["items"]] == ["Zzz Scanned"]
    row = by_barcode["items"][0]
    assert (row["matchedField"], row["matchQuality"], row["matchedText"], row["matchScore"]) == (
        "barcode", "exact", "8801", 1,
    )

    by_sku = await _search(client, tenant, "mysku-1")
    assert by_sku["items"][0]["name"] == "Aaa Typed Sku"
    assert by_sku["items"][0]["matchedField"] == "sku"
    assert by_sku["items"][0]["matchQuality"] == "exact"

    by_brand = await _search(client, tenant, "napa")
    top = by_brand["items"][0]
    assert top["name"] == "Napa"
    assert (top["matchedField"], top["matchQuality"]) == ("name", "exact")

    by_generic = await _search(client, tenant, "paracetamol")
    fields = [row["matchedField"] for row in by_generic["items"]]
    assert fields[0] == "genericName"
    assert set(fields) == {"genericName"}

    by_alias = await _search(client, tenant, "socid")
    assert [row["name"] for row in by_alias["items"]] == ["Seclo"]
    assert by_alias["items"][0]["matchedField"] == "alias"

    by_typo = await _search(client, tenant, "omeprazle")
    assert [row["name"] for row in by_typo["items"]] == ["Omeprazole"]
    fuzzy = by_typo["items"][0]
    assert fuzzy["matchedField"] == "name"
    assert fuzzy["matchQuality"] == "fuzzy"
    assert fuzzy["matchScore"] > 0.7


async def test_shop_status_breaks_rank_ties(
    client: Any, session: Any, tenant: dict[str, Any]
) -> None:
    """Two exact generic hits: the row already on this shelf outranks the absent one."""
    tablet = await _form(session, "Tablet")
    on_shelf = await _catalog_product(session, name="Present One", generic_name="Paracetamol", dosage_form_id=tablet.id)
    absent = await _catalog_product(session, name="Absent One", generic_name="Paracetamol", dosage_form_id=tablet.id)
    linked = PharmacyProduct(
        organization_id=tenant["organization"].id,
        catalog_product_id=on_shelf.id,
        name="Present One",
        unit="tablet",
        active=True,
    )
    session.add(linked)
    await session.flush()
    await _shelf_row(session, tenant, linked, sku="PRES-1")
    await session.commit()

    page = await _search(client, tenant, "paracetamol")
    assert [(row["name"], row["shopStatus"]) for row in page["items"]] == [
        ("Present One", "on_shelf"),
        ("Absent One", "absent"),
    ]


async def test_typo_and_supporting_queries(
    client: Any, session: Any, tenant: dict[str, Any]
) -> None:
    tablet = await _form(session, "Tablet")
    syrup = await _form(session, "Syrup")
    napa = await _catalog_product(
        session, name="Napa", generic_name="Paracetamol", strength="500 mg", dosage_form_id=tablet.id,
    )
    # Same form, different strength: the tie-break lands on the product name, so
    # the 500mg row a "napa 500 tablet" query is about stays distinguishable from
    # the paediatric one.
    await _catalog_product(session, name="Napa Kid", strength="120 mg", dosage_form_id=tablet.id)
    await _catalog_product(session, name="Napa Syrup Bottle", strength="100 mg", dosage_form_id=syrup.id)
    await session.commit()

    typo = await _search(client, tenant, "npa")
    # All three tie at fuzzy 0.75; the tie-break runs dosage form then name, so
    # the syrup leads on "syrup" < "tablet" and the tablets follow by name.
    assert [row["name"] for row in typo["items"]] == ["Napa Syrup Bottle", "Napa", "Napa Kid"]
    assert typo["items"][0]["matchQuality"] == "fuzzy"

    supporting = await _search(client, tenant, "napa 500 tablet")
    assert [row["name"] for row in supporting["items"]] == ["Napa"]
    assert supporting["items"][0]["matchedField"] == "name"

    excluded = await _search(client, tenant, "napa 650 tablet")
    assert excluded["items"] == []

    strength_only = await _search(client, tenant, "500 mg")
    assert [row["name"] for row in strength_only["items"]] == ["Napa"]
    assert strength_only["items"][0]["matchedField"] == "strength"
    assert strength_only["items"][0]["matchQuality"] == "supporting"

    form_only = await _search(client, tenant, "tablet")
    assert [row["name"] for row in form_only["items"]] == ["Napa", "Napa Kid"]
    assert form_only["items"][0]["matchedField"] == "dosageForm"

    # A form query must not surface rows of another form at all.
    syrup_only = await _search(client, tenant, "syrup")
    assert [row["name"] for row in syrup_only["items"]] == ["Napa Syrup Bottle"]


async def test_weak_lookalikes_and_short_fuzzy_queries_are_rejected(
    client: Any, session: Any, tenant: dict[str, Any]
) -> None:
    await _catalog_product(session, name="Napa")
    await session.commit()

    for query in ("naproxen", "yx"):
        page = await _search(client, tenant, query)
        assert page["items"] == [], query


async def test_case_and_spacing_normalization_on_the_wire(
    client: Any, session: Any, tenant: dict[str, Any]
) -> None:
    await _catalog_product(session, name="Napa Extra", strength="500 mg")
    await session.commit()

    page = await _search(client, tenant, "  NAPA   extra ")
    assert [row["name"] for row in page["items"]] == ["Napa Extra"]
    assert page["items"][0]["matchQuality"] == "exact"


async def test_custom_products_match_by_local_name_barcode_and_sku(
    client: Any, session: Any, tenant: dict[str, Any]
) -> None:
    custom = PharmacyProduct(
        organization_id=tenant["organization"].id,
        name="Khoirat Honey Mix",
        unit="bottle",
        barcode="777001",
        active=True,
    )
    session.add(custom)
    await session.flush()
    await _shelf_row(session, tenant, custom, sku="KH-1")
    await session.commit()

    by_name = await _search(client, tenant, "khoirat")
    assert [row["kind"] for row in by_name["items"]] == ["custom"]
    assert by_name["items"][0]["matchedField"] == "name"

    by_barcode = await _search(client, tenant, "777001")
    assert by_barcode["items"][0]["matchedField"] == "barcode"
    assert by_barcode["items"][0]["matchQuality"] == "exact"

    by_sku = await _search(client, tenant, "kh-1")
    assert by_sku["items"][0]["matchedField"] == "sku"
    assert by_sku["items"][0]["matchQuality"] == "exact"


async def test_search_pagination_is_flat_and_stable(
    client: Any, session: Any, tenant: dict[str, Any]
) -> None:
    for index in range(3):
        await _catalog_product(session, name=f"Paginate Brand {index}", generic_name="Paginate")
    await session.commit()

    first = await _search(client, tenant, "paginate", limit=2, offset=0)
    assert first["total"] == 3
    assert len(first["items"]) == 2
    second = await _search(client, tenant, "paginate", limit=2, offset=2)
    assert len(second["items"]) == 1
    assert second["items"][0]["name"] not in [row["name"] for row in first["items"]]


async def test_linked_dedup_keeps_one_row_for_catalog_and_org_product(
    client: Any, session: Any, tenant: dict[str, Any]
) -> None:
    tablet = await _form(session, "Tablet")
    catalog = await _catalog_product(
        session, name="Seclo 20", strength="20 mg", dosage_form_id=tablet.id,
    )
    linked = PharmacyProduct(
        organization_id=tenant["organization"].id,
        catalog_product_id=catalog.id,
        name="Local Stomach Pill",
        unit="capsule",
        active=True,
    )
    session.add(linked)
    await session.commit()

    page = await _search(client, tenant, "seclo 20")
    assert page["total"] == 1
    assert page["items"][0]["kind"] == "catalog"
    assert page["items"][0]["pharmacyProductId"] == str(linked.id)


# --- /products/current enrichment --------------------------------------------------


async def test_current_shelf_rows_carry_catalogue_identity(
    client: Any, session: Any, tenant: dict[str, Any]
) -> None:
    tablet = await _form(session, "Tablet")
    beximco = await _maker(session, "Shelf Beximco")
    catalog = await _catalog_product(
        session, name="Napa", generic_name="Paracetamol", strength="500 mg",
        dosage_form_id=tablet.id, manufacturer_id=beximco.id,
    )
    linked = PharmacyProduct(
        organization_id=tenant["organization"].id,
        catalog_product_id=catalog.id,
        name="Napa",
        unit="tablet",
        active=True,
    )
    session.add(linked)
    await session.flush()
    await _shelf_row(session, tenant, linked, sku="NAPA-500")
    custom = PharmacyProduct(
        organization_id=tenant["organization"].id,
        name="Khoirat Honey Mix",
        unit="bottle",
        active=True,
    )
    session.add(custom)
    await session.flush()
    await _shelf_row(session, tenant, custom, sku="KH-1")
    await session.commit()

    response = await client.get("/products/current", headers=_headers(tenant))
    assert response.status_code == 200, response.text
    items = {row["name"]: row for row in response.json()["data"]["items"]}

    linked_row = items["Napa"]
    assert linked_row["genericName"] == "Paracetamol"
    assert linked_row["strength"] == "500 mg"
    assert linked_row["manufacturer"] == "Shelf Beximco"
    assert linked_row["manufacturerId"] == str(beximco.id)
    assert linked_row["dosageForm"] == "Tablet"
    assert linked_row["dosageFormId"] == str(tablet.id)

    custom_row = items["Khoirat Honey Mix"]
    assert custom_row["genericName"] is None
    assert custom_row["manufacturer"] is None
    assert custom_row["dosageForm"] is None

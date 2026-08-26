"""CSV catalogue importer: parse, insert, update, and barcode dedupe."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from sqlalchemy import func, select

from app.domains.catalog import (
    ActiveIngredient,
    CatalogBarcode,
    CatalogProduct,
    CatalogRevision,
    DosageForm,
    Manufacturer,
)
from scripts.import_catalog_csv import import_rows, load_config, parse_rows

CONFIG = {
    "columnMap": {
        "name": "Product Name",
        "genericName": "Generic",
        "strength": "Strength",
        "dosageForm": "Form",
        "manufacturer": "Company",
        "packageSize": "Pack",
        "prescriptionRequired": "Rx",
        "barcodes": ["Barcode"],
    },
    "defaults": {"countryCode": "BD", "packageUnit": "piece"},
}

HEADERS = [
    "Product Name",
    "Generic",
    "Strength",
    "Form",
    "Company",
    "Pack",
    "Rx",
    "Barcode",
]


def _csv(path: Path, rows: list[list[str]]) -> Path:
    lines = [",".join(HEADERS)]
    for row in rows:
        lines.append(",".join(row))
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


async def test_parse_rows_maps_headers(tmp_path: Path) -> None:
    csv_path = _csv(tmp_path / "m.csv", [["Napa Extra", "Paracetamol", "500mg", "Tablet", "Beximco", "10", "yes", "880123"]])
    rows = parse_rows(csv_path, CONFIG)
    assert rows == [
        {
            "name": "Napa Extra",
            "genericName": "Paracetamol",
            "strength": "500mg",
            "dosageForm": "Tablet",
            "manufacturer": "Beximco",
            "packageSize": "10",
            "barcodes": ["880123"],
            "prescriptionRequired": True,
        }
    ]


async def test_import_insert_update_and_barcode_dedupe(
    session: Any, tmp_path: Path
) -> None:
    config = {"defaults": {"countryCode": "BD", "packageUnit": "tablet"}}
    rows = [
        {
            "name": "Imported Drug",
            "genericName": "Imported Molecule",
            "strength": "20mg",
            "manufacturer": "Import Co",
            "dosageForm": "Capsule",
            "barcodes": ["8805550001"],
        }
    ]
    summary = await import_rows(session, rows, config, dry_run=False)
    assert summary == {"created": 1, "updated": 0}

    product = (await session.scalars(select(CatalogProduct))).one()
    assert product.generic_name == "Imported Molecule"
    assert product.package_unit == "tablet"
    manufacturer = (await session.scalars(select(Manufacturer))).one()
    assert manufacturer.name == "Import Co"
    form = (await session.scalars(select(DosageForm))).one()
    assert form.name == "Capsule"
    ingredient = (await session.scalars(select(ActiveIngredient))).one()
    assert ingredient.name == "Imported Molecule"
    revisions = list(await session.scalars(select(CatalogRevision)))
    assert len(revisions) == 1

    # Same barcode, new label: the barcode wins the dedupe and updates in place.
    updated_rows = [
        {
            "name": "Imported Drug Renamed",
            "strength": "20mg",
            "barcodes": ["8805550001"],
        }
    ]
    summary = await import_rows(session, updated_rows, config, dry_run=False)
    assert summary == {"created": 0, "updated": 1}
    products = list(await session.scalars(select(CatalogProduct)))
    assert len(products) == 1
    assert products[0].name == "Imported Drug Renamed"
    revisions = list(await session.scalars(select(CatalogRevision)))
    assert len(revisions) == 2
    assert int(
        await session.scalar(select(func.count()).select_from(CatalogBarcode))
    ) == 1

    # A natural-key match with no barcode still dedupes: lower(name), strength,
    # dosage form, and country must all agree.
    summary = await import_rows(
        session,
        [{"name": "imported drug renamed", "strength": "20mg", "dosageForm": "Capsule"}],
        config,
        dry_run=False,
    )
    assert summary == {"created": 0, "updated": 1}
    assert int(await session.scalar(select(func.count()).select_from(CatalogProduct))) == 1


async def test_dry_run_commits_nothing(session: Any) -> None:
    config: dict[str, Any] = {"defaults": {"countryCode": "BD", "packageUnit": "piece"}}
    summary = await import_rows(
        session, [{"name": "Dry Row"}], config, dry_run=True
    )
    assert summary["created"] == 1
    assert int(await session.scalar(select(func.count()).select_from(CatalogProduct))) == 0


async def test_negative_and_garbage_prices_skipped_not_written(
    session: Any, capsys: Any
) -> None:
    config: dict[str, Any] = {"defaults": {"countryCode": "BD", "packageUnit": "piece"}}
    rows = [
        {"name": "Bad Prices", "unitPrice": "-5", "stripPrice": "cheap"},
    ]
    summary = await import_rows(session, rows, config, dry_run=False)
    assert summary == {"created": 1, "updated": 0}
    product = (await session.scalars(select(CatalogProduct))).one()
    assert product.unit_price is None
    assert product.strip_price is None
    warnings = capsys.readouterr().out
    assert "negative unit price" in warnings
    assert "not a number" in warnings


async def test_example_config_loads() -> None:
    config = load_config(Path(__file__).parents[1] / "scripts" / "catalog_import.example.json")
    mapping = json.loads(json.dumps(config))["columnMap"]
    assert "name" in mapping

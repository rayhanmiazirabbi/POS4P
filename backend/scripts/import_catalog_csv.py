"""Bulk-import global catalogue rows from a CSV file.

OPS-ONLY TOOL. ``catalog_products`` and its children are shared reference data
for every tenant on the platform; this script writes them directly, bypassing
the role-gated API. Run it against a database you own:

    uv run python scripts/import_catalog_csv.py --csv medicines.csv \
        --config scripts/catalog_import.example.json [--dry-run]

The config maps CSV headers onto catalogue fields (see the example file). Rows
upsert: a manufacturer, dosage form, or active ingredient is matched by name;
a catalogue product is matched first on any of its barcodes, then on
(lower(name), lower(strength), dosage form, country code). Existing products
are updated in place and get a CatalogRevision row; new ones start at revision 1.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.domains.catalog import (
    ActiveIngredient,
    CatalogBarcode,
    CatalogProduct,
    CatalogRevision,
    DosageForm,
    Manufacturer,
)
from app.security import utc_now

TRUTHY = frozenset({"true", "t", "yes", "y", "1", "rx"})


def load_config(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def parse_rows(csv_path: Path, config: dict[str, Any]) -> list[dict[str, Any]]:
    """Read the CSV and project headers onto catalogue field names."""
    mapping = config.get("columnMap", {})
    rows: list[dict[str, Any]] = []
    with csv_path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        for raw in reader:
            row: dict[str, Any] = {}
            for field in ("name", "genericName", "strength", "dosageForm", "manufacturer"):
                header = mapping.get(field)
                value = raw.get(header, "").strip() if header else ""
                if value:
                    row[field] = value
            size_header = mapping.get("packageSize")
            if size_header and raw.get(size_header, "").strip():
                row["packageSize"] = raw[size_header].strip()
            barcode_headers = mapping.get("barcodes") or []
            if isinstance(barcode_headers, str):
                barcode_headers = [barcode_headers]
            barcodes = [
                raw.get(header, "").strip() for header in barcode_headers if raw.get(header)
            ]
            if barcodes:
                row["barcodes"] = [code for code in barcodes if code]
            rx_header = mapping.get("prescriptionRequired")
            if rx_header:
                row["prescriptionRequired"] = raw.get(rx_header, "").strip().lower() in TRUTHY
            unit_header = mapping.get("unitPrice")
            if unit_header and raw.get(unit_header, "").strip():
                row["unitPrice"] = raw[unit_header].strip()
            strip_header = mapping.get("stripPrice")
            if strip_header and raw.get(strip_header, "").strip():
                row["stripPrice"] = raw[strip_header].strip()
            if row.get("name"):
                rows.append(row)
    return rows


def _decimal(value: str) -> Decimal | None:
    try:
        return Decimal(value)
    except InvalidOperation:
        return None


def _price_field(raw: str, label: str, product_name: str) -> Decimal | None:
    """Shared reference data gets the same non-negative rule the API enforces."""
    price = _decimal(raw)
    if price is None:
        print(f"warning: {label} {raw!r} for {product_name!r} is not a number; skipped")
        return None
    if price < 0:
        print(f"warning: negative {label} {raw!r} for {product_name!r}; skipped")
        return None
    return price


async def _first_scalar(session: AsyncSession, statement: Any) -> Any:
    return (await session.execute(statement)).scalar_one_or_none()


async def _upsert_reference(
    session: AsyncSession, model: Any, name: str
) -> Any:
    row = await _first_scalar(session, select(model).where(model.name == name))
    if row is None:
        row = model(name=name)
        session.add(row)
        await session.flush()
    return row


async def find_product(
    session: AsyncSession, row: dict[str, Any], country_code: str
) -> CatalogProduct | None:
    """Dedupe priority: any mapped barcode wins, then the natural key."""
    for code in row.get("barcodes", []):
        existing = await _first_scalar(
            session, select(CatalogBarcode).where(CatalogBarcode.barcode == code)
        )
        if existing is not None:
            product = await session.get(CatalogProduct, existing.catalog_product_id)
            if product is not None:
                return product
    dosage_form = row.get("dosageForm")
    form_id = None
    if dosage_form:
        form = await _first_scalar(
            session, select(DosageForm).where(DosageForm.name == dosage_form)
        )
        form_id = form.id if form is not None else None
    conditions = [
        func.lower(CatalogProduct.name) == row["name"].lower(),
        CatalogProduct.country_code == country_code,
    ]
    if row.get("strength"):
        conditions.append(func.lower(CatalogProduct.strength) == row["strength"].lower())
    else:
        conditions.append(CatalogProduct.strength.is_(None))
    conditions.append(
        CatalogProduct.dosage_form_id == form_id if form_id else CatalogProduct.dosage_form_id.is_(None)
    )
    return await _first_scalar(session, select(CatalogProduct).where(*conditions))


async def import_row(
    session: AsyncSession, row: dict[str, Any], config: dict[str, Any]
) -> str:
    """Import one parsed row; returns 'created' or 'updated'."""
    defaults = config.get("defaults", {})
    country_code = str(defaults.get("countryCode", "BD")).upper()
    package_unit = str(row.get("packageUnit") or defaults.get("packageUnit") or "piece")
    package_size = _decimal(str(row.get("packageSize") or defaults.get("packageSize") or "1"))
    package_size = package_size if package_size is not None else Decimal(1)

    manufacturer_id = None
    if row.get("manufacturer"):
        manufacturer_id = (
            await _upsert_reference(session, Manufacturer, row["manufacturer"])
        ).id
    dosage_form_id = None
    if row.get("dosageForm"):
        dosage_form_id = (await _upsert_reference(session, DosageForm, row["dosageForm"])).id

    product = await find_product(session, row, country_code)
    created = product is None
    if product is None:
        product = CatalogProduct(
            name=row["name"],
            country_code=country_code,
            package_unit=package_unit,
            package_size=package_size,
            active=True,
        )
        session.add(product)
        await session.flush()

    product.generic_name = row.get("genericName") or product.generic_name
    product.name = row.get("name") or product.name
    product.strength = row.get("strength") or product.strength
    product.manufacturer_id = manufacturer_id or product.manufacturer_id
    product.dosage_form_id = dosage_form_id or product.dosage_form_id
    product.package_unit = package_unit
    if row.get("prescriptionRequired") is not None:
        product.prescription_required = bool(row["prescriptionRequired"])
    if row.get("unitPrice"):
        price = _price_field(row["unitPrice"], "unit price", row["name"])
        if price is not None:
            product.unit_price = price
    if row.get("stripPrice"):
        price = _price_field(row["stripPrice"], "strip price", row["name"])
        if price is not None:
            product.strip_price = price
    await session.flush()

    existing_barcodes = set(
        await session.scalars(
            select(CatalogBarcode.barcode).where(
                CatalogBarcode.catalog_product_id == product.id
            )
        )
    )
    for code in row.get("barcodes", []):
        if code in existing_barcodes:
            continue
        clash = await _first_scalar(
            session, select(CatalogBarcode).where(CatalogBarcode.barcode == code)
        )
        if clash is not None:
            continue
        session.add(CatalogBarcode(catalog_product_id=product.id, barcode=code))

    if row.get("genericName"):
        ingredient = await _upsert_reference(session, ActiveIngredient, row["genericName"])
        from app.domains.catalog import CatalogProductIngredient

        link = await _first_scalar(
            session,
            select(CatalogProductIngredient).where(
                CatalogProductIngredient.catalog_product_id == product.id,
                CatalogProductIngredient.active_ingredient_id == ingredient.id,
            ),
        )
        if link is None:
            session.add(
                CatalogProductIngredient(
                    catalog_product_id=product.id, active_ingredient_id=ingredient.id
                )
            )

    last_revision = await _first_scalar(
        session,
        select(func.max(CatalogRevision.revision)).where(
            CatalogRevision.catalog_product_id == product.id
        ),
    )
    session.add(
        CatalogRevision(
            catalog_product_id=product.id,
            revision=(last_revision or 0) + 1,
            data={
                "name": product.name,
                "genericName": product.generic_name,
                "strength": product.strength,
                "packageSize": str(product.package_size),
                "packageUnit": product.package_unit,
                "unitPrice": str(product.unit_price) if product.unit_price is not None else None,
                "stripPrice": str(product.strip_price) if product.strip_price is not None else None,
                "countryCode": product.country_code,
                "source": "csv_import",
            },
            created_at=utc_now(),
        )
    )
    return "created" if created else "updated"


async def import_rows(
    session: AsyncSession, rows: list[dict[str, Any]], config: dict[str, Any], *, dry_run: bool
) -> dict[str, int]:
    summary = {"created": 0, "updated": 0}
    for row in rows:
        outcome = await import_row(session, row, config)
        summary[outcome] += 1
    if dry_run:
        await session.rollback()
    else:
        await session.commit()
    return summary


async def run(argv: list[str] | None = None) -> dict[str, int]:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", required=True, type=Path)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    config = load_config(args.config)
    rows = parse_rows(args.csv, config)
    async for session in get_session():
        summary = await import_rows(session, rows, config, dry_run=args.dry_run)
    print(
        f"{len(rows)} rows: {summary['created']} created, {summary['updated']} updated"
        + (" (dry run, nothing committed)" if args.dry_run else "")
    )
    return summary


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()

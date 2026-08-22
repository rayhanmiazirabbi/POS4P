from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.context import RequestContext
from app.domains.catalog import (
    ActiveIngredient,
    CatalogAlias,
    CatalogBarcode,
    CatalogProduct,
    CatalogProductIngredient,
    CatalogRevision,
    DosageForm,
    Manufacturer,
)
from app.errors import Conflict, NotFound
from app.schemas.catalog import (
    AliasIn,
    BarcodeIn,
    DosageFormCreateRequest,
    IngredientCreateRequest,
    ProductCreateRequest,
    ProductUpdateRequest,
    ReferenceCreateRequest,
    ReferenceUpdateRequest,
)
from app.services.audit import record_audit, redact

#: Reference-data and catalogue administration is owner/manager territory.
CATALOG_MANAGER_ACTIONS = ("catalog.created", "catalog.updated")


async def _commit_or_conflict(session: AsyncSession, message: str) -> None:
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise Conflict(message) from exc


def _now() -> datetime:
    return datetime.now(tz=UTC)


# --- reference data ---------------------------------------------------------


async def _load_reference(
    session: AsyncSession, model: type, entity_id: UUID, label: str
) -> Any:
    row = await session.get(model, entity_id)
    if row is None:
        raise NotFound(f"{label} not found")
    return row


async def create_manufacturer(
    session: AsyncSession,
    context: RequestContext,
    payload: ReferenceCreateRequest,
    *,
    request_id: str,
) -> Manufacturer:
    name = payload.clean_name
    existing = await session.scalar(select(Manufacturer.id).where(Manufacturer.name == name))
    if existing is not None:
        raise Conflict(f"Manufacturer '{name}' already exists")
    row = Manufacturer(
        name=name,
        country_code=payload.country_code.upper() if payload.country_code else None,
        active=payload.active,
    )
    session.add(row)
    await session.flush()
    record_audit(
        session,
        context,
        action="catalog.manufacturer_created",
        entity_type="manufacturer",
        entity_id=row.id,
        request_id=request_id,
        after=redact({"name": row.name, "country_code": row.country_code}),
    )
    await _commit_or_conflict(session, f"Manufacturer '{name}' already exists")
    return row


async def update_manufacturer(
    session: AsyncSession,
    context: RequestContext,
    entity_id: UUID,
    payload: ReferenceUpdateRequest,
    *,
    request_id: str,
) -> Manufacturer:
    row = await _load_reference(session, Manufacturer, entity_id, "Manufacturer")
    before = {"name": row.name, "country_code": row.country_code, "active": row.active}
    if payload.name is not None:
        row.name = payload.clean_name
    if payload.country_code is not None:
        row.country_code = payload.country_code.upper()
    if payload.active is not None:
        row.active = payload.active
    record_audit(
        session,
        context,
        action="catalog.manufacturer_updated",
        entity_type="manufacturer",
        entity_id=row.id,
        request_id=request_id,
        before=redact(before),
        after=redact({"name": row.name, "country_code": row.country_code, "active": row.active}),
    )
    await _commit_or_conflict(session, f"Manufacturer '{row.name}' already exists")
    return row


async def create_active_ingredient(
    session: AsyncSession,
    context: RequestContext,
    payload: IngredientCreateRequest,
    *,
    request_id: str,
) -> ActiveIngredient:
    name = payload.clean_name
    existing = await session.scalar(select(ActiveIngredient.id).where(ActiveIngredient.name == name))
    if existing is not None:
        raise Conflict(f"Active ingredient '{name}' already exists")
    row = ActiveIngredient(name=name)
    session.add(row)
    await session.flush()
    record_audit(
        session,
        context,
        action="catalog.ingredient_created",
        entity_type="active_ingredient",
        entity_id=row.id,
        request_id=request_id,
        after=redact({"name": row.name}),
    )
    await _commit_or_conflict(session, f"Active ingredient '{name}' already exists")
    return row


async def update_active_ingredient(
    session: AsyncSession,
    context: RequestContext,
    entity_id: UUID,
    payload: ReferenceUpdateRequest,
    *,
    request_id: str,
) -> ActiveIngredient:
    row = await _load_reference(session, ActiveIngredient, entity_id, "Active ingredient")
    before = {"name": row.name}
    if payload.name is not None:
        row.name = payload.clean_name
    record_audit(
        session,
        context,
        action="catalog.ingredient_updated",
        entity_type="active_ingredient",
        entity_id=row.id,
        request_id=request_id,
        before=redact(before),
        after=redact({"name": row.name}),
    )
    await _commit_or_conflict(session, f"Active ingredient '{row.name}' already exists")
    return row


async def create_dosage_form(
    session: AsyncSession,
    context: RequestContext,
    payload: DosageFormCreateRequest,
    *,
    request_id: str,
) -> DosageForm:
    name = payload.clean_name
    existing = await session.scalar(select(DosageForm.id).where(DosageForm.name == name))
    if existing is not None:
        raise Conflict(f"Dosage form '{name}' already exists")
    row = DosageForm(name=name)
    session.add(row)
    await session.flush()
    record_audit(
        session,
        context,
        action="catalog.dosage_form_created",
        entity_type="dosage_form",
        entity_id=row.id,
        request_id=request_id,
        after=redact({"name": row.name}),
    )
    await _commit_or_conflict(session, f"Dosage form '{name}' already exists")
    return row


async def update_dosage_form(
    session: AsyncSession,
    context: RequestContext,
    entity_id: UUID,
    payload: ReferenceUpdateRequest,
    *,
    request_id: str,
) -> DosageForm:
    row = await _load_reference(session, DosageForm, entity_id, "Dosage form")
    before = {"name": row.name}
    if payload.name is not None:
        row.name = payload.clean_name
    record_audit(
        session,
        context,
        action="catalog.dosage_form_updated",
        entity_type="dosage_form",
        entity_id=row.id,
        request_id=request_id,
        before=redact(before),
        after=redact({"name": row.name}),
    )
    await _commit_or_conflict(session, f"Dosage form '{row.name}' already exists")
    return row


async def list_manufacturers(session: AsyncSession) -> list[Manufacturer]:
    return list(await session.scalars(select(Manufacturer).order_by(Manufacturer.name)))


async def list_active_ingredients(session: AsyncSession) -> list[ActiveIngredient]:
    return list(await session.scalars(select(ActiveIngredient).order_by(ActiveIngredient.name)))


async def list_dosage_forms(session: AsyncSession) -> list[DosageForm]:
    return list(await session.scalars(select(DosageForm).order_by(DosageForm.name)))


# --- products ---------------------------------------------------------------


def product_snapshot(product: CatalogProduct) -> dict[str, Any]:
    return {
        "name": product.name,
        "manufacturerId": str(product.manufacturer_id) if product.manufacturer_id else None,
        "dosageFormId": str(product.dosage_form_id) if product.dosage_form_id else None,
        "strength": product.strength,
        "packageSize": str(product.package_size),
        "packageUnit": product.package_unit,
        "prescriptionRequired": product.prescription_required,
        "countryCode": product.country_code,
        "active": product.active,
    }


async def _append_revision(
    session: AsyncSession, context: RequestContext, product: CatalogProduct, request_id: str
) -> None:
    last = await session.scalar(
        select(func.max(CatalogRevision.revision)).where(
            CatalogRevision.catalog_product_id == product.id
        )
    )
    revision = CatalogRevision(
        catalog_product_id=product.id,
        revision=(last or 0) + 1,
        data=product_snapshot(product),
        changed_by_user_id=context.user_id,
        created_at=_now(),
    )
    session.add(revision)
    record_audit(
        session,
        context,
        action="catalog.product_created" if revision.revision == 1 else "catalog.product_updated",
        entity_type="catalog_product",
        entity_id=product.id,
        request_id=request_id,
        after=revision.data,
    )


async def _resolve_links(
    session: AsyncSession, manufacturer_id: UUID | None, dosage_form_id: UUID | None
) -> None:
    if manufacturer_id is not None and await session.get(Manufacturer, manufacturer_id) is None:
        raise NotFound("Manufacturer not found")
    if dosage_form_id is not None and await session.get(DosageForm, dosage_form_id) is None:
        raise NotFound("Dosage form not found")


async def _write_children(
    session: AsyncSession,
    product: CatalogProduct,
    ingredients: list[Any],
    barcodes: list[BarcodeIn],
    aliases: list[AliasIn],
) -> None:
    seen_ingredients: set[UUID] = set()
    for line in ingredients:
        ingredient_id = line.active_ingredient_id
        if ingredient_id in seen_ingredients:
            raise Conflict("Duplicate ingredient in combination")
        seen_ingredients.add(ingredient_id)
        if await session.get(ActiveIngredient, ingredient_id) is None:
            raise NotFound("Active ingredient not found")
        session.add(
            CatalogProductIngredient(
                catalog_product_id=product.id,
                active_ingredient_id=ingredient_id,
                strength=line.strength,
                unit=line.unit,
            )
        )
    for item in barcodes:
        session.add(CatalogBarcode(catalog_product_id=product.id, barcode=item.clean))
    for item in aliases:
        session.add(CatalogAlias(catalog_product_id=product.id, alias=item.clean))


async def create_product(
    session: AsyncSession,
    context: RequestContext,
    payload: ProductCreateRequest,
    *,
    request_id: str,
) -> CatalogProduct:
    await _resolve_links(session, payload.manufacturer_id, payload.dosage_form_id)
    product = CatalogProduct(
        name=payload.clean_name,
        manufacturer_id=payload.manufacturer_id,
        dosage_form_id=payload.dosage_form_id,
        strength=payload.strength,
        package_size=payload.package_size,
        package_unit=payload.package_unit,
        prescription_required=payload.prescription_required,
        country_code=payload.country_code.upper(),
        active=payload.active,
    )
    session.add(product)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise Conflict("Product could not be created") from exc
    try:
        await _write_children(session, product, payload.ingredients, payload.barcodes, payload.aliases)
        await _append_revision(session, context, product, request_id)
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise Conflict("Duplicate barcode, alias, or ingredient for this product") from exc
    return product


async def load_product(session: AsyncSession, product_id: UUID) -> CatalogProduct:
    product = await session.get(CatalogProduct, product_id)
    if product is None:
        raise NotFound("Catalog product not found")
    return product


async def update_product(
    session: AsyncSession,
    context: RequestContext,
    product_id: UUID,
    payload: ProductUpdateRequest,
    *,
    request_id: str,
) -> CatalogProduct:
    product = await load_product(session, product_id)
    changes = payload.model_dump(exclude_unset=True, exclude_none=True)
    if changes:
        await _resolve_links(
            session, changes.get("manufacturer_id"), changes.get("dosage_form_id")
        )
        if "country_code" in changes:
            changes["country_code"] = changes["country_code"].upper()
        for field, value in changes.items():
            setattr(product, field, value)
        try:
            await _append_revision(session, context, product, request_id)
            await session.commit()
        except IntegrityError as exc:
            await session.rollback()
            raise Conflict("Product could not be updated") from exc
    return product


async def product_detail(session: AsyncSession, product: CatalogProduct) -> dict[str, Any]:
    ingredients = list(
        await session.scalars(
            select(CatalogProductIngredient).where(
                CatalogProductIngredient.catalog_product_id == product.id
            )
        )
    )
    barcodes = list(
        await session.scalars(
            select(CatalogBarcode.barcode)
            .where(CatalogBarcode.catalog_product_id == product.id)
            .order_by(CatalogBarcode.barcode)
        )
    )
    aliases = list(
        await session.scalars(
            select(CatalogAlias.alias)
            .where(CatalogAlias.catalog_product_id == product.id)
            .order_by(CatalogAlias.alias)
        )
    )
    return {
        "product": product,
        "ingredients": ingredients,
        "barcodes": barcodes,
        "aliases": aliases,
    }


async def list_revisions(
    session: AsyncSession, product_id: UUID
) -> list[CatalogRevision]:
    await load_product(session, product_id)
    return list(
        await session.scalars(
            select(CatalogRevision)
            .where(CatalogRevision.catalog_product_id == product_id)
            .order_by(CatalogRevision.revision)
        )
    )


# --- search -----------------------------------------------------------------


async def search_products(
    session: AsyncSession,
    *,
    q: str | None = None,
    country_code: str | None = None,
    prescription_required: bool | None = None,
    active: bool | None = None,
    order: str = "newest",
    limit: int = 25,
    offset: int = 0,
) -> tuple[list[CatalogProduct], int]:
    """Substring search across name, alias, and exact barcode; shared reference data."""
    query = select(CatalogProduct)
    conditions = []
    if country_code is not None:
        conditions.append(CatalogProduct.country_code == country_code.upper())
    if prescription_required is not None:
        conditions.append(CatalogProduct.prescription_required == prescription_required)
    if active is not None:
        conditions.append(CatalogProduct.active == active)
    if q:
        needle = q.strip()
        if needle:
            alias_product_ids = (
                select(CatalogAlias.catalog_product_id).where(
                    CatalogAlias.alias.ilike(f"%{needle}%")
                )
            )
            barcode_product_ids = select(CatalogBarcode.catalog_product_id).where(
                CatalogBarcode.barcode == needle
            )
            conditions.append(
                or_(
                    CatalogProduct.name.ilike(f"%{needle}%"),
                    CatalogProduct.id.in_(alias_product_ids),
                    CatalogProduct.id.in_(barcode_product_ids),
                )
            )
    if conditions:
        query = query.where(*conditions)

    total = await session.scalar(
        select(func.count()).select_from(query.order_by(None).subquery())
    )
    if order == "alpha":
        query = query.order_by(CatalogProduct.name)
    else:
        query = query.order_by(CatalogProduct.created_at.desc())
    items = list(await session.scalars(query.offset(offset).limit(limit)))
    return items, int(total or 0)

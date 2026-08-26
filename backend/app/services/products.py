from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.context import RequestContext
from app.domains.catalog import (
    CatalogAlias,
    CatalogBarcode,
    CatalogProduct,
    DosageForm,
    Manufacturer,
)
from app.domains.inventory import InventoryBalance
from app.domains.products import PharmacyProduct, StoreProduct, StoreProductPrice
from app.errors import Conflict, NotFound, ValidationError
from app.models import Store
from app.schemas.products import (
    CatalogSearchItemResponse,
    PharmacyProductCreateRequest,
    PharmacyProductStatusRequest,
    PharmacyProductUpdateRequest,
    ProductAdoptRequest,
    ProductAdoptResponse,
    StoreProductEnableRequest,
    StoreProductStatusRequest,
    StoreProductUpdateRequest,
)
from app.services.audit import record_audit, redact
from app.services.stores import load_current_store, load_store

PRODUCT_MANAGER_ACTIONS = frozenset({"create", "update", "status"})


async def load_pharmacy_product(
    session: AsyncSession, context: RequestContext, product_id: UUID
) -> PharmacyProduct:
    """Fetch an organization product; another tenant's id reads as missing."""
    product = await session.get(PharmacyProduct, product_id)
    if product is None or product.organization_id != context.organization_id:
        raise NotFound("Product not found")
    return product


async def _validate_catalog_link(
    session: AsyncSession, organization_id: UUID, catalog_product_id: UUID | None
) -> None:
    if catalog_product_id is None:
        return
    catalog_product = await session.get(CatalogProduct, catalog_product_id)
    # The catalogue is global, so any known id would link -- but only active
    # entries are linkable and unknown ids are indistinguishable from foreign ones.
    if catalog_product is None or not catalog_product.active:
        raise NotFound("Catalog product not found")


async def _assert_barcode_free(
    session: AsyncSession, context: RequestContext, barcode: str | None, *, exclude: UUID | None
) -> None:
    """Barcode uniqueness is enforced among active products only."""
    if not barcode:
        return
    query = select(PharmacyProduct.id).where(
        PharmacyProduct.organization_id == context.organization_id,
        PharmacyProduct.barcode == barcode,
        PharmacyProduct.active.is_(True),
    )
    if exclude is not None:
        query = query.where(PharmacyProduct.id != exclude)
    existing = await session.scalar(query)
    if existing is not None:
        raise Conflict(f"Barcode '{barcode}' already exists in this organization")


async def list_pharmacy_products(
    session: AsyncSession, context: RequestContext, *, include_inactive: bool = False
) -> list[PharmacyProduct]:
    query = select(PharmacyProduct).where(
        PharmacyProduct.organization_id == context.organization_id
    )
    if not include_inactive:
        query = query.where(PharmacyProduct.active.is_(True))
    return list(await session.scalars(query.order_by(PharmacyProduct.name)))


async def create_pharmacy_product(
    session: AsyncSession,
    context: RequestContext,
    payload: PharmacyProductCreateRequest,
    *,
    request_id: str,
) -> PharmacyProduct:
    await _validate_catalog_link(session, context.organization_id, payload.catalog_product_id)
    await _assert_barcode_free(session, context, payload.barcode, exclude=None)

    product = PharmacyProduct(
        organization_id=context.organization_id,
        catalog_product_id=payload.catalog_product_id,
        name=payload.name,
        barcode=payload.barcode,
        unit=payload.unit,
        active=True,
    )
    session.add(product)
    try:
        await session.flush()
        record_audit(
            session,
            context,
            action="product.created",
            entity_type="pharmacy_product",
            entity_id=product.id,
            request_id=request_id,
            after=redact(
                {
                    "name": product.name,
                    "barcode": product.barcode,
                    "unit": product.unit,
                    "catalogProductId": str(product.catalog_product_id),
                }
            ),
        )
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise Conflict("Product barcode already exists in this organization") from exc
    except Exception:
        await session.rollback()
        raise
    return product


async def update_pharmacy_product(
    session: AsyncSession,
    context: RequestContext,
    product_id: UUID,
    payload: PharmacyProductUpdateRequest,
    *,
    request_id: str,
) -> PharmacyProduct:
    product = await load_pharmacy_product(session, context, product_id)
    changes = payload.model_dump(exclude_unset=True, exclude_none=True)
    if not changes:
        return product
    await _assert_barcode_free(
        session, context, changes.get("barcode"), exclude=product.id
    )

    before = {"name": product.name, "barcode": product.barcode, "unit": product.unit}
    for field in ("name", "barcode", "unit"):
        if field in changes:
            setattr(product, field, changes[field])
    record_audit(
        session,
        context,
        action="product.updated",
        entity_type="pharmacy_product",
        entity_id=product.id,
        request_id=request_id,
        before=redact(before),
        after=redact({"name": product.name, "barcode": product.barcode, "unit": product.unit}),
    )
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise Conflict("Product barcode already exists in this organization") from exc
    except Exception:
        await session.rollback()
        raise
    return product


async def update_pharmacy_product_status(
    session: AsyncSession,
    context: RequestContext,
    product_id: UUID,
    payload: PharmacyProductStatusRequest,
    *,
    request_id: str,
) -> PharmacyProduct:
    """Soft delete: historical references keep their rows; only the flag moves."""
    product = await load_pharmacy_product(session, context, product_id)
    if product.active == payload.active:
        return product
    before = {"active": product.active}
    product.active = payload.active
    record_audit(
        session,
        context,
        action="product.status_changed",
        entity_type="pharmacy_product",
        entity_id=product.id,
        request_id=request_id,
        before=redact(before),
        after=redact({"active": product.active}),
    )
    await session.commit()
    return product


# --- store products ---------------------------------------------------------


async def load_store_product(
    session: AsyncSession, context: RequestContext, store: Store, product_row_id: UUID
) -> StoreProduct:
    store_product = await session.get(StoreProduct, product_row_id)
    if (
        store_product is None
        or store_product.organization_id != context.organization_id
        or store_product.store_id != store.id
    ):
        raise NotFound("Store product not found")
    return store_product


def local_effective_at() -> datetime:
    return datetime.now(tz=UTC)


async def _assert_sku_free_in_store(
    session: AsyncSession, store: Store, sku: str, *, exclude: UUID | None
) -> None:
    query = select(StoreProduct.id).where(
        StoreProduct.store_id == store.id,
        StoreProduct.sku == sku,
        StoreProduct.active.is_(True),
    )
    if exclude is not None:
        query = query.where(StoreProduct.id != exclude)
    existing = await session.scalar(query)
    if existing is not None:
        raise Conflict(f"SKU '{sku}' already exists in this store")


async def _load_store_product_by_pharmacy_id(
    session: AsyncSession, store: Store, pharmacy_product_id: UUID
) -> StoreProduct | None:
    return await session.scalar(
        select(StoreProduct).where(
            StoreProduct.store_id == store.id,
            StoreProduct.pharmacy_product_id == pharmacy_product_id,
        )
    )


async def list_store_products(
    session: AsyncSession,
    context: RequestContext,
    store: Store,
    *,
    include_inactive: bool = False,
) -> list[StoreProduct]:
    query = select(StoreProduct).where(StoreProduct.store_id == store.id)
    if not include_inactive:
        query = query.where(StoreProduct.active.is_(True))
    return list(await session.scalars(query.order_by(StoreProduct.sku)))


async def list_shelf(
    session: AsyncSession,
    context: RequestContext,
    store: Store,
    *,
    include_inactive: bool = False,
) -> list[tuple[StoreProduct, PharmacyProduct]]:
    """The shelf, each row paired with the product it sells.

    One query with a join rather than ``list_store_products`` plus a lookup per
    row: the shelf is the list a counter loads on every start, and a shop with two
    thousand SKUs would otherwise open with two thousand round trips to the
    database.

    An inactive *pharmacy* product is excluded even when ``include_inactive`` asks
    for inactive shelf rows. The two flags mean different things -- the shelf row
    says "this branch stocks it", the product says "we sell this at all" -- and a
    product withdrawn organization-wide is not sellable at any branch that happens
    to have left its row switched on.
    """
    query = (
        select(StoreProduct, PharmacyProduct)
        .join(PharmacyProduct, PharmacyProduct.id == StoreProduct.pharmacy_product_id)
        .where(StoreProduct.store_id == store.id, PharmacyProduct.active.is_(True))
    )
    if not include_inactive:
        query = query.where(StoreProduct.active.is_(True))
    rows = await session.execute(query.order_by(StoreProduct.sku))
    return [(shelf_row, product) for shelf_row, product in rows.all()]


async def enable_store_product(
    session: AsyncSession,
    context: RequestContext,
    store: Store,
    payload: StoreProductEnableRequest,
    *,
    request_id: str,
) -> tuple[StoreProduct, bool]:
    """Enable (or re-enable) a pharmacy product on a store shelf.

    Returns the row plus whether it was newly created. An inactive row is
    re-activated with the supplied settings instead of raising a duplicate error.
    """
    pharmacy_product = await load_pharmacy_product(
        session, context, payload.pharmacy_product_id
    )
    await _assert_sku_free_in_store(session, store, payload.sku, exclude=None)

    existing = await _load_store_product_by_pharmacy_id(
        session, store, pharmacy_product.id
    )
    created = existing is None
    if created:
        store_product = StoreProduct(
            organization_id=context.organization_id,
            store_id=store.id,
            pharmacy_product_id=pharmacy_product.id,
            sku=payload.sku,
            sale_price=payload.sale_price,
            minimum_stock=payload.minimum_stock,
            rack=payload.rack,
            active=True,
        )
        session.add(store_product)
    else:
        store_product = existing
        assert store_product is not None
        before_active = store_product.active
        store_product.sku = payload.sku
        price_changed = store_product.sale_price != payload.sale_price
        old_price = store_product.sale_price
        store_product.sale_price = payload.sale_price
        store_product.minimum_stock = payload.minimum_stock
        store_product.rack = payload.rack
        store_product.active = True
        if price_changed and before_active:
            _append_price_history(session, context, store_product, old_price)
    record_audit(
        session,
        replace_store_context(context, store),
        action="store_product.enabled" if created else "store_product.re_enabled",
        entity_type="store_product",
        entity_id=store_product.id,
        request_id=request_id,
        after=redact(
            {
                "sku": store_product.sku,
                "salePrice": str(store_product.sale_price),
                "minimumStock": str(store_product.minimum_stock),
                "rack": store_product.rack,
            }
        ),
    )
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise Conflict("SKU already exists in this store") from exc
    except Exception:
        await session.rollback()
        raise
    return store_product, created


def replace_store_context(context: RequestContext, store: Store) -> RequestContext:
    from dataclasses import replace as dataclass_replace

    return dataclass_replace(context, store_id=store.id)


def _append_price_history(
    session: AsyncSession,
    context: RequestContext,
    store_product: StoreProduct,
    previous_price: Decimal,
) -> StoreProductPrice:
    row = StoreProductPrice(
        organization_id=store_product.organization_id,
        store_id=store_product.store_id,
        store_product_id=store_product.id,
        price=previous_price,
        effective_at=local_effective_at(),
        actor_user_id=context.user_id,
    )
    session.add(row)
    return row


async def update_store_product(
    session: AsyncSession,
    context: RequestContext,
    store: Store,
    product_row_id: UUID,
    payload: StoreProductUpdateRequest,
    *,
    request_id: str,
) -> StoreProduct:
    store_product = await load_store_product(session, context, store, product_row_id)
    changes = payload.model_dump(exclude_unset=True)
    if not changes:
        return store_product
    if "sku" in changes:
        await _assert_sku_free_in_store(
            session, store, changes["sku"], exclude=store_product.id
        )

    before = {
        "sku": store_product.sku,
        "salePrice": str(store_product.sale_price),
        "minimumStock": str(store_product.minimum_stock),
        "rack": store_product.rack,
    }
    old_price = store_product.sale_price
    for field in ("sku", "sale_price", "minimum_stock", "rack"):
        if field in changes:
            setattr(store_product, field, changes[field])

    record_audit(
        session,
        replace_store_context(context, store),
        action="store_product.updated",
        entity_type="store_product",
        entity_id=store_product.id,
        request_id=request_id,
        before=redact(before),
        after=redact(
            {
                "sku": store_product.sku,
                "salePrice": str(store_product.sale_price),
                "minimumStock": str(store_product.minimum_stock),
                "rack": store_product.rack,
            }
        ),
    )
    if old_price != store_product.sale_price:
        _append_price_history(session, context, store_product, old_price)
        record_audit(
            session,
            replace_store_context(context, store),
            action="store_product.price_changed",
            entity_type="store_product",
            entity_id=store_product.id,
            request_id=request_id,
            before=redact({"price": str(old_price)}),
            after=redact({"price": str(store_product.sale_price)}),
        )
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise Conflict("SKU already exists in this store") from exc
    except Exception:
        await session.rollback()
        raise
    return store_product


async def update_store_product_status(
    session: AsyncSession,
    context: RequestContext,
    store: Store,
    product_row_id: UUID,
    payload: StoreProductStatusRequest,
    *,
    request_id: str,
) -> StoreProduct:
    store_product = await load_store_product(session, context, store, product_row_id)
    if store_product.active == payload.active:
        return store_product
    store_product.active = payload.active
    record_audit(
        session,
        replace_store_context(context, store),
        action="store_product.status_changed",
        entity_type="store_product",
        entity_id=store_product.id,
        request_id=request_id,
        before=redact({"active": not payload.active}),
        after=redact({"active": store_product.active}),
    )
    await session.commit()
    return store_product


async def list_price_history(
    session: AsyncSession,
    context: RequestContext,
    store: Store,
    product_row_id: UUID,
) -> list[StoreProductPrice]:
    store_product = await load_store_product(session, context, store, product_row_id)
    return list(
        await session.scalars(
            select(StoreProductPrice)
            .where(StoreProductPrice.store_product_id == store_product.id)
            .order_by(StoreProductPrice.effective_at.desc())
        )
    )


async def count_store_products(session: AsyncSession, store: Store) -> int:
    """Historical reference count used to prove deactivation never deletes rows."""
    return int(
        await session.scalar(
            select(func.count())
            .select_from(StoreProduct)
            .where(StoreProduct.store_id == store.id)
        )
    )


# --- unified catalogue search -------------------------------------------------

#: Candidate ceiling for each side of a search (catalogue and org products).
#: The merge and rank happen in Python, so an unbounded fetch would let one
#: broad query drag the whole org product list plus the catalogue through
#: memory; stage 3 replaces this with proper full-text ranking (pg_trgm).
SEARCH_MATCH_CAP = 500


async def _matched_catalog_products(session: AsyncSession, needle: str) -> list[CatalogProduct]:
    alias_ids = select(CatalogAlias.catalog_product_id).where(
        CatalogAlias.alias.ilike(f"%{needle}%")
    )
    barcode_ids = select(CatalogBarcode.catalog_product_id).where(CatalogBarcode.barcode == needle)
    return list(
        (
            await session.scalars(
                select(CatalogProduct)
                .where(
                    CatalogProduct.active.is_(True),
                    or_(
                        CatalogProduct.name.ilike(f"%{needle}%"),
                        CatalogProduct.generic_name.ilike(f"%{needle}%"),
                        CatalogProduct.id.in_(alias_ids),
                        CatalogProduct.id.in_(barcode_ids),
                    ),
                )
                .order_by(CatalogProduct.name)
                .limit(SEARCH_MATCH_CAP)
            )
        ).all()
    )


def _search_rank(needle: str) -> Callable[[CatalogSearchItemResponse], tuple[int, str]]:
    """Exact barcode hits first, then alphabetical -- the order a counter expects."""

    def key(item: CatalogSearchItemResponse) -> tuple[int, str]:
        barcode = item.barcode or ""
        exact = barcode.strip().lower() == needle.lower()
        return (0 if exact else 1, item.name.lower())

    return key


async def search_unified(
    session: AsyncSession,
    context: RequestContext,
    store: Store,
    *,
    q: str,
    limit: int = 25,
    offset: int = 0,
) -> tuple[list[CatalogSearchItemResponse], int]:
    """One search box across the shared catalogue and the org's own products.

    A pharmacy product linked to a catalogue entry renders as a single ``catalog``
    row carrying its shelf status; unlinked org products render as ``custom`` rows.
    Status is relative to ``store``: ``on_shelf`` needs an active shelf row, then
    ``in_org``, then ``absent``.
    """
    needle = q.strip()
    if not needle:
        raise ValidationError("A search term is required")

    matched_pharmacy = list(
        await session.scalars(
            select(PharmacyProduct)
            .where(
                PharmacyProduct.organization_id == context.organization_id,
                PharmacyProduct.active.is_(True),
                or_(
                    PharmacyProduct.name.ilike(f"%{needle}%"),
                    PharmacyProduct.barcode == needle,
                ),
            )
            .order_by(PharmacyProduct.name)
            .limit(SEARCH_MATCH_CAP)
        )
    )

    matched_catalog = await _matched_catalog_products(session, needle)
    catalog_ids = {row.id for row in matched_catalog}
    catalog_ids.update(p.catalog_product_id for p in matched_pharmacy if p.catalog_product_id)

    catalog_rows: dict[UUID, CatalogProduct] = {row.id: row for row in matched_catalog}
    if catalog_ids - set(catalog_rows):
        linked = list(
            await session.scalars(
                select(CatalogProduct).where(CatalogProduct.id.in_(catalog_ids))
            )
        )
        for linked_catalog in linked:
            catalog_rows[linked_catalog.id] = linked_catalog

    # The full linkage picture, not just the matches: a catalogue row whose org
    # product exists but did not itself match must still report in_org.
    org_by_catalog: dict[UUID, PharmacyProduct] = {}
    if catalog_ids:
        linked_org = list(
            await session.scalars(
                select(PharmacyProduct).where(
                    PharmacyProduct.organization_id == context.organization_id,
                    PharmacyProduct.active.is_(True),
                    PharmacyProduct.catalog_product_id.in_(catalog_ids),
                )
            )
        )
        for linked_row in linked_org:
            assert linked_row.catalog_product_id is not None
            org_by_catalog.setdefault(linked_row.catalog_product_id, linked_row)

    pharmacy_ids = {p.id for p in matched_pharmacy if p.catalog_product_id is None}
    pharmacy_ids |= {p.id for p in org_by_catalog.values()}
    shelf_by_product: dict[UUID, tuple[StoreProduct, Decimal]] = {}
    if pharmacy_ids:
        shelf_rows = list(
            await session.execute(
                select(StoreProduct, InventoryBalance)
                .outerjoin(InventoryBalance, InventoryBalance.store_product_id == StoreProduct.id)
                .where(
                    StoreProduct.store_id == store.id,
                    StoreProduct.pharmacy_product_id.in_(pharmacy_ids),
                )
            )
        )
        for shelf_row, balance in shelf_rows:
            available = (
                Decimal(balance.on_hand) - Decimal(balance.reserved)
                if balance is not None
                else Decimal(0)
            )
            shelf_by_product[shelf_row.pharmacy_product_id] = (shelf_row, available)

    barcode_of: dict[UUID, str] = {}
    if catalog_ids:
        barcode_rows = await session.scalars(
            select(CatalogBarcode).where(CatalogBarcode.catalog_product_id.in_(catalog_ids))
        )
        for barcode_row in barcode_rows:
            barcode_of.setdefault(barcode_row.catalog_product_id, barcode_row.barcode)

    manufacturer_ids = {row.manufacturer_id for row in catalog_rows.values() if row.manufacturer_id}
    form_ids = {row.dosage_form_id for row in catalog_rows.values() if row.dosage_form_id}
    manufacturers = {
        row.id: row.name
        for row in await session.scalars(select(Manufacturer).where(Manufacturer.id.in_(manufacturer_ids)))
    } if manufacturer_ids else {}
    forms = {
        row.id: row.name
        for row in await session.scalars(select(DosageForm).where(DosageForm.id.in_(form_ids)))
    } if form_ids else {}

    items: list[CatalogSearchItemResponse] = []
    claimed_products: set[UUID] = set()

    def attach_shop(
        item: CatalogSearchItemResponse, product: PharmacyProduct | None
    ) -> None:
        if product is None or product.active is False:
            return
        item.pharmacy_product_id = product.id
        item.shop_status = "in_org"
        claimed_products.add(product.id)
        if product.barcode and item.barcode is None:
            item.barcode = product.barcode
        shelf = shelf_by_product.get(product.id)
        if shelf is not None and shelf[0].active:
            shelf_row, available = shelf
            item.kind = "catalog" if item.catalog_product_id else "custom"
            item.store_product_id = shelf_row.id
            item.sku = shelf_row.sku
            item.sale_price = shelf_row.sale_price
            item.available_quantity = available
            item.shop_status = "on_shelf"

    rendered_catalog: set[UUID] = set()
    for catalog_id, catalog_row in sorted(catalog_rows.items(), key=lambda pair: pair[1].name.lower()):
        if not catalog_row.active:
            # Inactive catalogue entries stay out of the results; their org
            # products fall through to the custom loop below instead of
            # vanishing with the entry.
            continue
        rendered_catalog.add(catalog_id)
        item = CatalogSearchItemResponse(
            kind="catalog",
            catalog_product_id=catalog_row.id,
            name=catalog_row.name,
            generic_name=catalog_row.generic_name,
            strength=catalog_row.strength,
            barcode=barcode_of.get(catalog_row.id),
            dosage_form_id=catalog_row.dosage_form_id,
            dosage_form=forms.get(catalog_row.dosage_form_id) if catalog_row.dosage_form_id else None,
            manufacturer_id=catalog_row.manufacturer_id,
            manufacturer=manufacturers.get(catalog_row.manufacturer_id) if catalog_row.manufacturer_id else None,
            package_size=catalog_row.package_size,
            package_unit=catalog_row.package_unit,
            prescription_required=catalog_row.prescription_required,
            reference_unit_price=catalog_row.unit_price,
            reference_strip_price=catalog_row.strip_price,
        )
        attach_shop(item, org_by_catalog.get(catalog_id))
        items.append(item)

    for product in matched_pharmacy:
        if product.id in claimed_products or product.catalog_product_id in rendered_catalog:
            continue
        item = CatalogSearchItemResponse(
            kind="custom",
            name=product.name,
            barcode=product.barcode,
            package_unit=product.unit,
        )
        attach_shop(item, product)
        items.append(item)

    items.sort(key=_search_rank(needle))
    total = len(items)
    return items[offset : offset + limit], total


# --- adoption -----------------------------------------------------------------


def _sku_seed(catalog: CatalogProduct) -> str:
    text = f"{catalog.name} {catalog.strength or ''}"
    slug = "".join(char for char in text.upper() if char.isalnum())[:12]
    return slug or "ITEM"


async def _generate_sku(session: AsyncSession, store: Store, catalog: CatalogProduct) -> str:
    """Deterministic SKU from the catalogue identity; suffixed until unique.

    Uniqueness is checked against every row ever created in the store, not just
    active ones -- the database constraint does not care whether the colliding
    row is on the shelf. The seed is alphanumeric only, so the LIKE prefix
    cannot be reinterpreted as wildcards.
    """
    seed = _sku_seed(catalog)
    taken = set(
        await session.scalars(
            select(StoreProduct.sku).where(
                StoreProduct.store_id == store.id,
                StoreProduct.sku.like(f"{seed}%"),
            )
        )
    )
    candidate = seed
    suffix = 1
    while candidate in taken:
        suffix += 1
        candidate = f"{seed}-{suffix}"
    return candidate


async def adopt_catalog_product(
    session: AsyncSession,
    context: RequestContext,
    payload: ProductAdoptRequest,
    *,
    request_id: str,
) -> ProductAdoptResponse:
    """Bring a catalogue entry into this shop and onto a branch shelf.

    Reuses an existing org product for the entry (reactivating it when it was
    soft-deleted) so repeated adoption never forks duplicates. Barcode auto-fill
    is best-effort: the org-wide unique-active-barcode constraint can lose a race
    with another branch adopting at the same moment, and losing that race must
    never fail the adoption itself.
    """
    catalog = await session.get(CatalogProduct, payload.catalog_product_id)
    if catalog is None or not catalog.active:
        raise NotFound("Catalog product not found")

    store = (
        await load_store(session, context, payload.store_id)
        if payload.store_id is not None
        else await load_current_store(session, context)
    )

    sale_price = payload.sale_price if payload.sale_price is not None else catalog.unit_price
    if sale_price is None:
        raise ValidationError(
            "Adoption needs a price: pass salePrice or set unitPrice on the catalogue entry"
        )

    product = await session.scalar(
        select(PharmacyProduct).where(
            PharmacyProduct.organization_id == context.organization_id,
            PharmacyProduct.catalog_product_id == catalog.id,
        )
    )
    reactivated = False
    created_product = False
    if product is not None:
        reactivated = not product.active
        product.active = True
        session.add(product)
    else:
        created_product = True
        product = PharmacyProduct(
            organization_id=context.organization_id,
            catalog_product_id=catalog.id,
            name=catalog.name,
            unit=catalog.package_unit,
            active=True,
        )
        session.add(product)
        await session.flush()

    existing_shelf = await _load_store_product_by_pharmacy_id(session, store, product.id)
    created_shelf = existing_shelf is None
    old_price: Decimal | None = None
    sku = payload.sku
    if existing_shelf is None:
        shelf_row = StoreProduct(
            organization_id=context.organization_id,
            store_id=store.id,
            pharmacy_product_id=product.id,
            sku=sku or await _generate_sku(session, store, catalog),
            sale_price=sale_price,
            minimum_stock=payload.minimum_stock,
            rack=payload.rack,
            active=True,
        )
        session.add(shelf_row)
    else:
        shelf_row = existing_shelf
        shelf_row.active = True
        if sku is not None:
            await _assert_sku_free_in_store(session, store, sku, exclude=shelf_row.id)
            shelf_row.sku = sku
        old_price = shelf_row.sale_price
        shelf_row.sale_price = sale_price
        shelf_row.minimum_stock = payload.minimum_stock
        if payload.rack is not None:
            shelf_row.rack = payload.rack

    record_audit(
        session,
        replace_store_context(context, store),
        action="product.adopted",
        entity_type="pharmacy_product",
        entity_id=product.id,
        request_id=request_id,
        after=redact(
            {
                "catalogProductId": str(catalog.id),
                "pharmacyProductId": str(product.id),
                "storeProductId": str(shelf_row.id),
                "sku": shelf_row.sku,
                "salePrice": str(shelf_row.sale_price),
                "createdProduct": created_product,
                "createdShelfRow": created_shelf,
                "reactivated": reactivated,
            }
        ),
    )
    if old_price is not None and old_price != shelf_row.sale_price:
        # Parity with enable/update: a changed price on a live shelf row leaves a
        # history entry behind -- in the same transaction as the change itself.
        _append_price_history(session, replace_store_context(context, store), shelf_row, old_price)

    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise Conflict("Adoption conflicts with an existing product or SKU") from exc
    except Exception:
        await session.rollback()
        raise

    if created_product:
        # Best-effort and separate from the adoption commit: losing the barcode
        # race must never roll back an adopted product.
        await _autofill_barcode(session, context, product, request_id=request_id)

    return ProductAdoptResponse.model_validate(
        {"pharmacy_product": product, "store_product": shelf_row}
    )


async def _autofill_barcode(
    session: AsyncSession,
    context: RequestContext,
    product: PharmacyProduct,
    *,
    request_id: str,
) -> None:
    """Copy the first catalogue barcode onto a freshly adopted org product."""
    first = await session.scalar(
        select(CatalogBarcode.barcode)
        .where(CatalogBarcode.catalog_product_id == product.catalog_product_id)
        .order_by(CatalogBarcode.barcode)
        .limit(1)
    )
    if first is None or not await _barcode_free(session, context, first):
        return
    product.barcode = first
    try:
        await session.commit()
    except IntegrityError:
        # Another branch won the race; the adoption stands without a barcode.
        await session.rollback()
        product.barcode = None


async def _barcode_free(
    session: AsyncSession, context: RequestContext, barcode: str | None
) -> bool:
    if not barcode:
        return False
    existing = await session.scalar(
        select(PharmacyProduct.id).where(
            PharmacyProduct.organization_id == context.organization_id,
            PharmacyProduct.barcode == barcode,
            PharmacyProduct.active.is_(True),
        )
    )
    return existing is None

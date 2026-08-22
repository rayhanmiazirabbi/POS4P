from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.context import RequestContext
from app.domains.catalog import CatalogProduct
from app.domains.products import PharmacyProduct, StoreProduct, StoreProductPrice
from app.errors import Conflict, NotFound
from app.models import Store
from app.schemas.products import (
    PharmacyProductCreateRequest,
    PharmacyProductStatusRequest,
    PharmacyProductUpdateRequest,
    StoreProductEnableRequest,
    StoreProductStatusRequest,
    StoreProductUpdateRequest,
)
from app.services.audit import record_audit, redact
from app.services.stores import load_store

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
    return datetime.now(tz=timezone.utc)


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

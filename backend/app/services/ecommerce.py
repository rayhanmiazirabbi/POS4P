from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.context import RequestContext
from app.domains.catalog import CatalogProduct
from app.domains.ecommerce import EcommerceProductSetting, Storefront
from app.domains.products import PharmacyProduct, StoreProduct
from app.errors import Conflict, NotFound, ValidationError
from app.models import Organization, Role
from app.services import billing
from app.services.audit import record_audit


def assert_listing_role(context: RequestContext) -> None:
    if context.role not in {Role.OWNER, Role.MANAGER}:
        from app.errors import Forbidden

        raise Forbidden("Only owners and managers manage storefront listings")


async def load_storefront(
    session: AsyncSession, context: RequestContext, storefront_id: UUID
) -> Storefront:
    storefront = await session.get(Storefront, storefront_id)
    if (
        storefront is None
        or storefront.organization_id != context.organization_id
        or (context.store_id is not None and storefront.store_id != context.store_id)
    ):
        raise NotFound("Storefront not found")
    return storefront


async def upsert_storefront(
    session: AsyncSession,
    context: RequestContext,
    payload,
    *,
    request_id: str,
) -> Storefront:
    """Create or update the one storefront a branch exposes; no product data is copied."""
    assert_listing_role(context)
    store_id = _store_id(context)
    existing = await session.scalar(
        select(Storefront).where(
            Storefront.organization_id == context.organization_id,
            Storefront.store_id == store_id,
            Storefront.slug == payload.slug,
        )
    )
    if existing is None:
        # The (organization_id, slug) index spans branches: a second branch
        # reusing a sibling's slug must be a 409, not an unhandled 500.
        slug_clash = await session.scalar(
            select(Storefront).where(
                Storefront.organization_id == context.organization_id,
                Storefront.slug == payload.slug,
                Storefront.store_id != store_id,
            )
        )
        if slug_clash is not None:
            raise Conflict("Storefront slug is already used by another branch of this organization")
    if payload.custom_domain is not None:
        # A vanity domain is a paid feature: the entitlement is checked here, at
        # the exact mutation that would use it, not merely in the UI.
        await billing.ensure_entitlement(session, context.organization_id, "custom_domain")
        # Checked on updates too -- moving a storefront onto a domain another
        # tenant already owns is exactly when the clash happens.
        domain_query = select(Storefront).where(
            Storefront.custom_domain == payload.custom_domain
        )
        if existing is not None:
            domain_query = domain_query.where(Storefront.id != existing.id)
        domain_clash = await session.scalar(domain_query)
        if domain_clash is not None:
            raise Conflict("Custom domain already belongs to another storefront")

    created = existing is None
    storefront = existing or Storefront(
        organization_id=context.organization_id,
        store_id=store_id,
        slug=payload.slug,
    )
    storefront.display_name = payload.display_name
    storefront.enabled = payload.enabled
    storefront.custom_domain = payload.custom_domain
    session.add(storefront)
    try:
        await session.flush()
    except IntegrityError as exc:
        # A concurrent upsert won the race for the slug or domain.
        await session.rollback()
        raise Conflict("Storefront slug or custom domain is already in use") from exc
    record_audit(
        session,
        context,
        action="ecommerce.storefront_created" if created else "ecommerce.storefront_updated",
        entity_type="storefront",
        entity_id=storefront.id,
        request_id=request_id,
        after={"slug": storefront.slug, "enabled": storefront.enabled},
    )
    await session.commit()
    return storefront


async def list_storefronts(
    session: AsyncSession, context: RequestContext
) -> list[Storefront]:
    scope = [Storefront.organization_id == context.organization_id]
    if context.store_id is not None:
        scope.append(Storefront.store_id == context.store_id)
    return list(
        await session.scalars(
            select(Storefront).where(*scope).order_by(Storefront.created_at, Storefront.id)
        )
    )


async def load_store_product(
    session: AsyncSession, context: RequestContext, store_product_id: UUID
) -> StoreProduct:
    store_product = await session.get(StoreProduct, store_product_id)
    if (
        store_product is None
        or store_product.organization_id != context.organization_id
        or (context.store_id is not None and store_product.store_id != context.store_id)
    ):
        raise NotFound("Store product not found")
    return store_product


async def upsert_listing(
    session: AsyncSession,
    context: RequestContext,
    store_product_id: UUID,
    payload,
    *,
    request_id: str,
) -> EcommerceProductSetting:
    """Enable a store product online by writing only display overrides.

    The catalogue and POS rows stay untouched: an online price or name here is a
    per-branch overlay resolved at read time, never a copied product record.
    """
    assert_listing_role(context)
    store_product = await load_store_product(session, context, store_product_id)
    if not store_product.active:
        raise ValidationError("Inactive products cannot be listed online")
    setting = await session.scalar(
        select(EcommerceProductSetting).where(
            EcommerceProductSetting.store_id == store_product.store_id,
            EcommerceProductSetting.store_product_id == store_product.id,
        )
    )
    created = setting is None
    setting = setting or EcommerceProductSetting(
        organization_id=context.organization_id,
        store_id=store_product.store_id,
        store_product_id=store_product.id,
    )
    setting.online_name = payload.online_name
    setting.description = payload.description
    setting.online_price = payload.online_price
    setting.listed = payload.listed
    setting.pickup_enabled = payload.pickup_enabled
    setting.delivery_enabled = payload.delivery_enabled
    session.add(setting)
    await session.flush()
    record_audit(
        session,
        context,
        action="ecommerce.listing_created" if created else "ecommerce.listing_updated",
        entity_type="ecommerce_product_setting",
        entity_id=setting.id,
        request_id=request_id,
        after={"listed": setting.listed, "online_price": str(payload.online_price)},
    )
    await session.commit()
    return setting


async def list_listings(
    session: AsyncSession,
    context: RequestContext,
    *,
    listed: bool | None = None,
) -> list[EcommerceProductSetting]:
    scope = [EcommerceProductSetting.organization_id == context.organization_id]
    if context.store_id is not None:
        scope.append(EcommerceProductSetting.store_id == context.store_id)
    if listed is not None:
        scope.append(EcommerceProductSetting.listed.is_(listed))
    return list(
        await session.scalars(
            select(EcommerceProductSetting)
            .where(*scope)
            .order_by(EcommerceProductSetting.created_at, EcommerceProductSetting.id)
        )
    )


async def resolve_public_storefront(
    session: AsyncSession, organization_slug: str, slug: str
) -> Storefront:
    """Resolve an enabled storefront from its public address.

    No token involved: the organization slug plus the storefront slug *is* the
    tenant scope for guest traffic. Anything else (disabled storefront, unknown
    org) reads as a 404 so probing reveals nothing.
    """
    row = (
        await session.execute(
            select(Storefront, Organization)
            .join(Organization, Organization.id == Storefront.organization_id)
            .where(Organization.slug == organization_slug, Storefront.slug == slug)
        )
    ).first()
    if row is None or not row[0].enabled:
        raise NotFound("Storefront not found")
    return row[0]


async def catalogue_items(session: AsyncSession, storefront: Storefront) -> list[dict]:
    """The live listing rows of one branch, overlaid and prescription-flagged."""
    rows = (
        await session.execute(
            select(StoreProduct, EcommerceProductSetting, PharmacyProduct, CatalogProduct)
            .join(
                EcommerceProductSetting,
                EcommerceProductSetting.store_product_id == StoreProduct.id,
                isouter=True,
            )
            .join(
                PharmacyProduct,
                PharmacyProduct.id == StoreProduct.pharmacy_product_id,
            )
            .join(
                CatalogProduct,
                CatalogProduct.id == PharmacyProduct.catalog_product_id,
                isouter=True,
            )
            .where(
                StoreProduct.store_id == storefront.store_id,
                StoreProduct.active.is_(True),
                EcommerceProductSetting.listed.is_(True),
            )
            .order_by(StoreProduct.sku)
        )
    ).all()

    items: list[dict] = []
    for store_product, setting, pharmacy_product, catalog_product in rows:
        items.append(
            {
                "store_product_id": store_product.id,
                "name": setting.online_name or pharmacy_product.name,
                "price": setting.online_price or store_product.sale_price,
                "pickup_enabled": setting.pickup_enabled,
                "delivery_enabled": setting.delivery_enabled,
                "prescription_required": bool(
                    catalog_product is not None and catalog_product.prescription_required
                ),
            }
        )
    return items


async def public_catalogue(
    session: AsyncSession,
    context: RequestContext,
    slug: str,
):
    """Resolve the live catalogue for a storefront slug (staff view).

    Reads active store products of the storefront's branch, overlays the listing's
    online name/price where present, and flags prescription-only items so clients
    can gate checkout without ever bypassing the pharmacist review.
    """
    storefront = await session.scalar(
        select(Storefront).where(
            Storefront.organization_id == context.organization_id,
            Storefront.slug == slug,
        )
    )
    if storefront is None or not storefront.enabled:
        raise NotFound("Storefront not found")
    if context.store_id is not None and storefront.store_id != context.store_id:
        raise NotFound("Storefront not found")
    return storefront, await catalogue_items(session, storefront)


def _store_id(context: RequestContext) -> UUID:
    if context.store_id is None:
        raise ValidationError("Store context required", code="STORE_CONTEXT_REQUIRED")
    return context.store_id

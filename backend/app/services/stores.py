from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.context import RequestContext
from app.errors import Conflict, Forbidden, NotFound, ValidationError
from app.models import RecordStatus, Role, Store, StoreUser
from app.schemas.organizations import OrganizationSettings
from app.schemas.stores import (
    StoreCreateRequest,
    StoreSettings,
    StoreSettingsUpdate,
    StoreStatusUpdateRequest,
    StoreUpdateRequest,
)
from app.services.audit import record_audit, redact
from app.services.organizations import (
    coerce_settings,
    get_organization,
    merge_settings,
    require_active_organization,
)

#: Roles whose organization membership already grants every branch in the tenant.
ORGANIZATION_WIDE_ROLES = frozenset({Role.OWNER, Role.MANAGER})

#: MVP scope: a single branch per tenant. Stage 2 lifts this with transfers.
MAX_STORES_PER_ORGANIZATION = 1


def store_settings_of(store: Store) -> StoreSettings:
    return coerce_settings(StoreSettings, store.settings)


def local_now(store: Store, *, moment: datetime | None = None) -> datetime:
    """Wall-clock time at the branch. Reports are cut on this, not on server time."""
    return (moment or datetime.now(tz=ZoneInfo("UTC"))).astimezone(ZoneInfo(store.timezone))


def business_date(store: Store, *, moment: datetime | None = None) -> date:
    """The trading day a moment belongs to, honouring the branch cutoff hour."""
    cutoff = store_settings_of(store).business_day_cutoff_hour
    return (local_now(store, moment=moment) - timedelta(hours=cutoff)).date()


def is_operational(store: Store) -> bool:
    return store.status is RecordStatus.ACTIVE


def require_operational_store(store: Store) -> Store:
    """Gate for trading operations: a closed or suspended branch must not transact."""
    if not is_operational(store):
        raise Forbidden(f"Store '{store.code}' is {store.status.value} and cannot be used")
    return store


async def _assert_store_membership(
    session: AsyncSession, context: RequestContext, store: Store
) -> None:
    """Re-check branch access on every scoped operation.

    Owners and managers reach every branch through their organization membership;
    everyone else needs an explicit active ``store_users`` row, so a token that
    names a branch the user was removed from stops working immediately.
    """
    if context.role in ORGANIZATION_WIDE_ROLES:
        return
    assignment = await session.scalar(
        select(StoreUser.id).where(
            StoreUser.store_id == store.id,
            StoreUser.user_id == context.user_id,
            StoreUser.active.is_(True),
        )
    )
    if assignment is None:
        raise Forbidden("Store access denied")


async def load_store(session: AsyncSession, context: RequestContext, store_id: UUID) -> Store:
    """Fetch a branch by id under the caller's tenant.

    A branch belonging to another organization is reported as missing rather than
    forbidden, so direct object probing cannot confirm that the id exists.
    """
    store = await session.get(Store, store_id)
    if store is None or store.organization_id != context.organization_id:
        raise NotFound("Store not found")
    await _assert_store_membership(session, context, store)
    return store


async def load_current_store(session: AsyncSession, context: RequestContext) -> Store:
    if context.store_id is None:
        raise ValidationError("Store context required", code="STORE_CONTEXT_REQUIRED")
    return await load_store(session, context, context.store_id)


async def list_stores(session: AsyncSession, context: RequestContext) -> list[Store]:
    """Branches the caller may switch into -- the safe input for a store picker."""
    stores = list(
        await session.scalars(
            select(Store)
            .where(Store.organization_id == context.organization_id)
            .order_by(Store.code)
        )
    )
    if context.role in ORGANIZATION_WIDE_ROLES:
        return stores
    assigned = set(
        await session.scalars(
            select(StoreUser.store_id).where(
                StoreUser.user_id == context.user_id,
                StoreUser.active.is_(True),
            )
        )
    )
    return [store for store in stores if store.id in assigned]


async def create_store(
    session: AsyncSession,
    context: RequestContext,
    payload: StoreCreateRequest,
    *,
    request_id: str,
) -> Store:
    """Create the tenant's branch, defaulting timezone/currency from organization settings."""
    organization = require_active_organization(await get_organization(session, context))
    existing = list(
        await session.scalars(
            select(Store.code).where(Store.organization_id == context.organization_id)
        )
    )
    if payload.code in existing:
        raise Conflict(f"Store code '{payload.code}' already exists in this organization")
    if len(existing) >= MAX_STORES_PER_ORGANIZATION:
        raise Conflict("This plan supports a single store per organization")

    defaults = coerce_settings(OrganizationSettings, organization.settings)
    store = Store(
        organization_id=context.organization_id,
        name=payload.name.strip(),
        code=payload.code,
        timezone=payload.timezone or defaults.default_timezone,
        currency=payload.currency or defaults.default_currency,
        status=RecordStatus.ACTIVE,
        settings=merge_settings(
            StoreSettings, None, payload.settings or StoreSettingsUpdate()
        ).model_dump(),
    )
    session.add(store)
    try:
        await session.flush()
        record_audit(
            session,
            replace(context, store_id=store.id),
            action="store.created",
            entity_type="store",
            entity_id=store.id,
            request_id=request_id,
            after=redact(
                {
                    "name": store.name,
                    "code": store.code,
                    "timezone": store.timezone,
                    "currency": store.currency,
                }
            ),
        )
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise Conflict("Store code already exists in this organization") from exc
    except Exception:
        await session.rollback()
        raise
    return store


def _assert_mutable(store: Store) -> None:
    """A suspended branch is an administrative lock; only status may be changed."""
    if store.status is RecordStatus.SUSPENDED:
        raise Forbidden("Store is suspended; reactivate it before changing its configuration")


async def update_store(
    session: AsyncSession,
    context: RequestContext,
    store_id: UUID,
    payload: StoreUpdateRequest,
    *,
    request_id: str,
) -> Store:
    store = await load_store(session, context, store_id)
    _assert_mutable(store)
    changes = payload.model_dump(exclude_unset=True, exclude_none=True)
    if not changes:
        return store

    before = {"name": store.name, "timezone": store.timezone, "currency": store.currency}
    for field in ("name", "timezone", "currency"):
        if field in changes:
            setattr(store, field, changes[field].strip() if field == "name" else changes[field])
    record_audit(
        session,
        replace(context, store_id=store.id),
        action="store.updated",
        entity_type="store",
        entity_id=store.id,
        request_id=request_id,
        before=redact(before),
        after=redact({"name": store.name, "timezone": store.timezone, "currency": store.currency}),
    )
    await session.commit()
    return store


async def update_store_settings(
    session: AsyncSession,
    context: RequestContext,
    store_id: UUID,
    patch: StoreSettingsUpdate,
    *,
    request_id: str,
) -> tuple[Store, StoreSettings]:
    store = await load_store(session, context, store_id)
    _assert_mutable(store)
    before = store_settings_of(store)
    merged = merge_settings(StoreSettings, store.settings, patch)
    store.settings = merged.model_dump()
    record_audit(
        session,
        replace(context, store_id=store.id),
        action="store.settings_updated",
        entity_type="store",
        entity_id=store.id,
        request_id=request_id,
        before=redact(before.model_dump()),
        after=redact(merged.model_dump()),
    )
    await session.commit()
    return store, merged


async def update_store_status(
    session: AsyncSession,
    context: RequestContext,
    store_id: UUID,
    payload: StoreStatusUpdateRequest,
    *,
    request_id: str,
) -> Store:
    store = await load_store(session, context, store_id)
    target = RecordStatus(payload.status)
    before = store.status
    if before is target:
        return store
    store.status = target
    record_audit(
        session,
        replace(context, store_id=store.id),
        action="store.status_changed",
        entity_type="store",
        entity_id=store.id,
        request_id=request_id,
        before=redact({"status": before.value}),
        after=redact({"status": target.value, "reason": payload.reason}),
    )
    await session.commit()
    return store

from __future__ import annotations

from collections.abc import Mapping
from typing import Annotated, Any
from uuid import UUID

from fastapi import Depends, Header
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.context import RequestContext
from app.db import get_session
from app.errors import Conflict, Forbidden, NotFound, Unauthorized
from app.models import Organization, OrganizationUser, RecordStatus, Role, Store, User
from app.models import Session as SessionModel
from app.schemas.base import ApiModel
from app.schemas.organizations import (
    OrganizationCreateRequest,
    OrganizationSettings,
    OrganizationSettingsUpdate,
    OrganizationUpdateRequest,
    slugify,
)
from app.security import as_utc, utc_now, verify_access_token
from app.services.audit import record_audit, redact


def coerce_settings[M: ApiModel](model: type[M], stored: Mapping[str, Any] | None) -> M:
    """Overlay persisted settings onto the current defaults.

    Keys left behind by an older release are dropped instead of raising, so a
    settings read never fails because the schema moved on.
    """
    known = {key: value for key, value in (stored or {}).items() if key in model.model_fields}
    return model(**known)


def merge_settings[M: ApiModel](
    model: type[M], stored: Mapping[str, Any] | None, patch: ApiModel
) -> M:
    """Apply only the keys the client actually sent, so PATCH cannot reset a sibling."""
    current = coerce_settings(model, stored).model_dump()
    current.update(patch.model_dump(exclude_unset=True))
    return model(**current)


async def resolve_actor_user(
    session: Annotated[AsyncSession, Depends(get_session)],
    authorization: Annotated[str | None, Header()] = None,
) -> User:
    """Authenticate the caller *without* requiring an existing tenant context.

    Organization creation is the one operation that cannot use ``ContextDep``,
    because the organization it would authorize against does not exist yet. The
    session row, its revocation/expiry state, and the user's status are still
    re-verified, and the actor comes only from the signed token -- never the body.
    """
    if not authorization or not authorization.lower().startswith("bearer "):
        raise Unauthorized("Authentication required")
    claims = verify_access_token(authorization[7:].strip())
    if claims is None:
        raise Unauthorized("Access token is invalid or expired")
    try:
        session_id = UUID(str(claims["sid"]))
        user_id = UUID(str(claims["sub"]))
    except (KeyError, ValueError) as exc:
        raise Unauthorized("Access token is malformed") from exc

    auth_session = await session.get(SessionModel, session_id)
    if auth_session is None or auth_session.revoked_at is not None:
        raise Unauthorized("Session has been revoked")
    expires_at = as_utc(auth_session.expires_at)
    if expires_at is not None and expires_at <= utc_now():
        raise Unauthorized("Session has expired")
    if auth_session.user_id != user_id:
        raise Unauthorized("Session does not match the presented token")

    user = await session.get(User, user_id)
    if user is None or user.status is not RecordStatus.ACTIVE:
        raise Unauthorized("User account is not active")
    return user


ActorDep = Annotated[User, Depends(resolve_actor_user)]


async def _assert_slug_available(
    session: AsyncSession, slug: str, *, exclude: UUID | None = None
) -> None:
    query = select(Organization.id).where(Organization.slug == slug)
    if exclude is not None:
        query = query.where(Organization.id != exclude)
    if await session.scalar(query) is not None:
        raise Conflict(f"Organization slug '{slug}' is already taken")


async def create_organization(
    session: AsyncSession,
    actor: User,
    payload: OrganizationCreateRequest,
    *,
    request_id: str,
    device_id: UUID | None = None,
) -> tuple[Organization, OrganizationUser]:
    """Create the tenant and its first owner membership in one transaction.

    A half-applied bootstrap would leave an organization nobody can administer
    while still holding its globally unique slug, so the rows and the audit entry
    commit together or not at all.
    """
    slug = payload.slug or slugify(payload.name)
    if not slug:
        raise Conflict("Organization name does not yield a usable slug")
    await _assert_slug_available(session, slug)

    settings = merge_settings(
        OrganizationSettings, None, payload.settings or OrganizationSettingsUpdate()
    )
    organization = Organization(
        name=payload.name.strip(),
        slug=slug,
        status=RecordStatus.ACTIVE,
        settings=settings.model_dump(),
    )
    session.add(organization)
    try:
        await session.flush()
        membership = OrganizationUser(
            organization_id=organization.id,
            user_id=actor.id,
            role=Role.OWNER,
            active=True,
        )
        session.add(membership)
        record_audit(
            session,
            RequestContext(
                organization_id=organization.id,
                user_id=actor.id,
                role=Role.OWNER,
                device_id=device_id,
            ),
            action="organization.created",
            entity_type="organization",
            entity_id=organization.id,
            request_id=request_id,
            after=redact(
                {
                    "name": organization.name,
                    "slug": organization.slug,
                    "ownerUserId": str(actor.id),
                }
            ),
        )
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise Conflict("Organization slug is already taken") from exc
    except Exception:
        await session.rollback()
        raise
    return organization, membership


async def get_organization(session: AsyncSession, context: RequestContext) -> Organization:
    """Load the tenant named by the validated context -- never by a client-supplied id."""
    organization = await session.get(Organization, context.organization_id)
    if organization is None:
        raise NotFound("Organization not found")
    return organization


async def read_settings(session: AsyncSession, context: RequestContext) -> OrganizationSettings:
    organization = await get_organization(session, context)
    return coerce_settings(OrganizationSettings, organization.settings)


def require_active_organization(organization: Organization) -> Organization:
    """Gate mutations behind subscription state.

    A suspended tenant (unpaid invoice, compliance hold) stays readable so the
    client can explain the lock, but it must not accept configuration changes.
    """
    if organization.status is not RecordStatus.ACTIVE:
        raise Forbidden(f"Organization is {organization.status.value}")
    return organization


async def update_organization(
    session: AsyncSession,
    context: RequestContext,
    payload: OrganizationUpdateRequest,
    *,
    request_id: str,
) -> Organization:
    organization = require_active_organization(await get_organization(session, context))
    changes = payload.model_dump(exclude_unset=True, exclude_none=True)
    if not changes:
        return organization

    before = {"name": organization.name, "slug": organization.slug}
    if "slug" in changes and changes["slug"] != organization.slug:
        await _assert_slug_available(session, changes["slug"], exclude=organization.id)
        organization.slug = changes["slug"]
    if "name" in changes:
        organization.name = changes["name"].strip()
    record_audit(
        session,
        context,
        action="organization.updated",
        entity_type="organization",
        entity_id=organization.id,
        request_id=request_id,
        before=redact(before),
        after=redact({"name": organization.name, "slug": organization.slug}),
    )
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise Conflict("Organization slug is already taken") from exc
    return organization


async def update_settings(
    session: AsyncSession,
    context: RequestContext,
    patch: OrganizationSettingsUpdate,
    *,
    request_id: str,
) -> OrganizationSettings:
    organization = require_active_organization(await get_organization(session, context))
    before = coerce_settings(OrganizationSettings, organization.settings)
    merged = merge_settings(OrganizationSettings, organization.settings, patch)
    organization.settings = merged.model_dump()
    record_audit(
        session,
        context,
        action="organization.settings_updated",
        entity_type="organization",
        entity_id=organization.id,
        request_id=request_id,
        before=redact(before.model_dump()),
        after=redact(merged.model_dump()),
    )
    await session.commit()
    return merged


async def current_context(
    session: AsyncSession, context: RequestContext
) -> tuple[Organization, Store | None]:
    """Resolve the organization plus the store the access token is pinned to, if any."""
    organization = await get_organization(session, context)
    store: Store | None = None
    if context.store_id is not None:
        store = await session.scalar(
            select(Store).where(
                Store.id == context.store_id,
                Store.organization_id == context.organization_id,
            )
        )
    return organization, store

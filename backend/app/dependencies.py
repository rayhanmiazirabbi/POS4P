from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import Depends, Header, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.context import RequestContext
from app.db import get_session
from app.errors import Forbidden, Unauthorized, ValidationError
from app.models import OrganizationUser, RecordStatus, Role, Session as SessionModel, Store, User
from app.security import as_utc, utc_now

SessionDep = Annotated[AsyncSession, Depends(get_session)]


def _unauthorized(message: str = "Authentication required") -> Unauthorized:
    return Unauthorized(message)


async def resolve_context(
    request: Request,
    session: SessionDep,
    authorization: Annotated[str | None, Header()] = None,
) -> RequestContext:
    """Turn a bearer access token into a validated tenant context.

    The organization, store, and role are read from the signed token and then
    re-verified against live membership rows, so a revoked session, deactivated
    user, or suspended store cannot keep operating on a still-valid token.
    """
    from app.security import verify_access_token

    if not authorization or not authorization.lower().startswith("bearer "):
        raise _unauthorized()
    claims = verify_access_token(authorization[7:].strip())
    if claims is None:
        raise _unauthorized("Access token is invalid or expired")

    try:
        session_id = UUID(str(claims["sid"]))
        user_id = UUID(str(claims["sub"]))
        organization_id = UUID(str(claims["org"]))
    except (KeyError, ValueError) as exc:
        raise _unauthorized("Access token is malformed") from exc

    store_id = UUID(str(claims["store"])) if claims.get("store") else None
    device_id = UUID(str(claims["dev"])) if claims.get("dev") else None

    auth_session = await session.get(SessionModel, session_id)
    if auth_session is None or auth_session.revoked_at is not None:
        raise _unauthorized("Session has been revoked")
    expires_at = as_utc(auth_session.expires_at)
    if expires_at is not None and expires_at <= utc_now():
        raise _unauthorized("Session has expired")
    if auth_session.user_id != user_id or auth_session.organization_id != organization_id:
        raise _unauthorized("Session does not match the presented token")

    user = await session.get(User, user_id)
    if user is None or user.status is not RecordStatus.ACTIVE:
        raise _unauthorized("User account is not active")

    membership = await session.scalar(
        select(OrganizationUser).where(
            OrganizationUser.organization_id == organization_id,
            OrganizationUser.user_id == user_id,
            OrganizationUser.active.is_(True),
        )
    )
    if membership is None:
        raise Forbidden("Organization access denied")

    if store_id is not None:
        store = await session.get(Store, store_id)
        if store is None or store.organization_id != organization_id:
            raise Forbidden("Store access denied")

    context = RequestContext(
        organization_id=organization_id,
        user_id=user_id,
        role=membership.role,
        store_id=store_id,
        device_id=device_id,
    )
    request.state.context = context
    return context


ContextDep = Annotated[RequestContext, Depends(resolve_context)]


async def resolve_store_context(context: ContextDep) -> RequestContext:
    if context.store_id is None:
        raise ValidationError("Store context required", code="STORE_CONTEXT_REQUIRED")
    return context


StoreContextDep = Annotated[RequestContext, Depends(resolve_store_context)]


def require_roles(*allowed: Role):
    """Route dependency enforcing role membership. Unknown roles default to deny."""

    async def guard(context: ContextDep) -> RequestContext:
        if context.role not in allowed:
            raise Forbidden("Capability denied")
        return context

    return guard


def get_request_id(request: Request) -> str:
    return getattr(request.state, "request_id", "unknown")


RequestIdDep = Annotated[str, Depends(get_request_id)]


async def get_idempotency_key(
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> str | None:
    if idempotency_key is not None and not 16 <= len(idempotency_key.strip()) <= 128:
        raise ValidationError("Idempotency-Key must be between 16 and 128 characters")
    return idempotency_key.strip() if idempotency_key else None


IdempotencyKeyDep = Annotated[str | None, Depends(get_idempotency_key)]

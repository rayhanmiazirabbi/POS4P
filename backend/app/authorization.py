from __future__ import annotations

from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import OrganizationUser, Role, StoreUser


async def require_organization_member(session: AsyncSession, organization_id: UUID, user_id: UUID) -> OrganizationUser:
    membership = await session.scalar(select(OrganizationUser).where(
        OrganizationUser.organization_id == organization_id,
        OrganizationUser.user_id == user_id,
        OrganizationUser.active.is_(True),
    ))
    if membership is None:
        raise HTTPException(status_code=403, detail={"code": "FORBIDDEN", "message": "Organization access denied"})
    return membership


async def require_store_member(session: AsyncSession, organization_id: UUID, store_id: UUID, user_id: UUID) -> StoreUser:
    membership = await session.scalar(select(StoreUser).join(StoreUser.store).where(
        StoreUser.store_id == store_id,
        StoreUser.user_id == user_id,
        StoreUser.active.is_(True),
    ))
    if membership is None or membership.store.organization_id != organization_id:
        raise HTTPException(status_code=403, detail={"code": "FORBIDDEN", "message": "Store access denied"})
    return membership


def require_role(role: Role, *allowed: Role) -> None:
    if role not in allowed:
        raise HTTPException(status_code=403, detail={"code": "FORBIDDEN", "message": "Capability denied"})

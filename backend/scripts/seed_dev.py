"""Seed one owner, one organization, one store for local development.

Usage:
    uv run python scripts/seed_dev.py

Idempotent: skips anything whose phone/slug/code already exists.
"""

import asyncio

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import engine, get_session
from app.models.identity import Organization, OrganizationUser, Role, Store, StoreUser, User

PHONE = "+8801700000001"
ORG_SLUG = "dev-pharmacy"
STORE_CODE = "MAIN"


async def seed(session: AsyncSession) -> None:
    user = (await session.execute(select(User).where(User.phone == PHONE))).scalar_one_or_none()
    if user is None:
        user = User(phone=PHONE, display_name="Dev Owner", pin_hash=None)
        session.add(user)
        await session.flush()
        print(f"created user {user.phone}")
    else:
        print(f"user {user.phone} exists, reusing")

    org = (await session.execute(select(Organization).where(Organization.slug == ORG_SLUG))).scalar_one_or_none()
    if org is None:
        org = Organization(name="Dev Pharmacy", slug=ORG_SLUG, settings={})
        session.add(org)
        await session.flush()
        print(f"created organization {org.slug}")
    else:
        print(f"organization {org.slug} exists, reusing")

    store = (await session.execute(select(Store).where(Store.code == STORE_CODE, Store.organization_id == org.id))).scalar_one_or_none()
    if store is None:
        store = Store(organization_id=org.id, name="Main Branch", code=STORE_CODE, timezone="Asia/Dhaka", currency="BDT", settings={})
        session.add(store)
        await session.flush()
        print(f"created store {store.code}")
    else:
        print(f"store {store.code} exists, reusing")

    membership = (
        await session.execute(
            select(OrganizationUser).where(OrganizationUser.organization_id == org.id, OrganizationUser.user_id == user.id)
        )
    ).scalar_one_or_none()
    if membership is None:
        session.add(OrganizationUser(organization_id=org.id, user_id=user.id, role=Role.OWNER, active=True))
        print("created organization membership (owner)")
    else:
        print("organization membership exists")

    store_user = (await session.execute(select(StoreUser).where(StoreUser.store_id == store.id, StoreUser.user_id == user.id))).scalar_one_or_none()
    if store_user is None:
        session.add(StoreUser(store_id=store.id, user_id=user.id, role=Role.OWNER, active=True))
        print("created store membership (owner)")
    else:
        print("store membership exists")

    await session.commit()


async def main() -> None:
    async for session in get_session():
        await seed(session)
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())

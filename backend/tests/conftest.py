from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from typing import Any
from uuid import UUID

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db import get_session
from app.main import app
from app.models import (
    Base,
    Organization,
    OrganizationUser,
    Role,
    Store,
    StoreUser,
    User,
)
from app.security import hash_secret, sign_access_token


@pytest_asyncio.fixture
async def engine() -> AsyncIterator[Any]:
    """A fresh in-memory schema per test.

    SQLite stands in for PostgreSQL because the models use portable ``Uuid``/``JSON``
    types and non-native enums. Anything relying on PostgreSQL-specific behaviour
    (row locking, ``FOR UPDATE SKIP LOCKED``) must be asserted against a real
    PostgreSQL instance instead.
    """
    created = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with created.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield created
    await created.dispose()


@pytest_asyncio.fixture
async def session(engine: Any) -> AsyncIterator[AsyncSession]:
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as db:
        yield db


@pytest_asyncio.fixture
async def client(engine: Any) -> AsyncIterator[AsyncClient]:
    """HTTP client bound to the same in-memory database as the ``session`` fixture."""
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    async def override() -> AsyncIterator[AsyncSession]:
        async with factory() as db:
            yield db

    app.dependency_overrides[get_session] = override
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        yield http
    app.dependency_overrides.clear()


# --- factories -------------------------------------------------------------


@pytest.fixture
def make_organization(session: AsyncSession) -> Callable[..., Any]:
    async def factory(name: str = "Test Pharmacy", slug: str = "test-pharmacy", **kwargs: Any) -> Organization:
        organization = Organization(name=name, slug=slug, settings={}, **kwargs)
        session.add(organization)
        await session.flush()
        return organization

    return factory


@pytest.fixture
def make_user(session: AsyncSession) -> Callable[..., Any]:
    async def factory(
        phone: str = "+8801700000000",
        display_name: str = "Test User",
        pin: str | None = None,
        **kwargs: Any,
    ) -> User:
        user = User(
            phone=phone,
            display_name=display_name,
            pin_hash=hash_secret(pin) if pin else None,
            **kwargs,
        )
        session.add(user)
        await session.flush()
        return user

    return factory


@pytest.fixture
def make_store(session: AsyncSession) -> Callable[..., Any]:
    async def factory(organization: Organization, name: str = "Main Branch", code: str = "MAIN", **kwargs: Any) -> Store:
        store = Store(
            organization_id=organization.id,
            name=name,
            code=code,
            timezone="Asia/Dhaka",
            currency="BDT",
            settings={},
            **kwargs,
        )
        session.add(store)
        await session.flush()
        return store

    return factory


@pytest.fixture
def make_membership(session: AsyncSession) -> Callable[..., Any]:
    async def factory(
        organization: Organization, user: User, role: Role = Role.OWNER, store: Store | None = None
    ) -> OrganizationUser:
        membership = OrganizationUser(
            organization_id=organization.id, user_id=user.id, role=role, active=True
        )
        session.add(membership)
        if store is not None:
            session.add(StoreUser(store_id=store.id, user_id=user.id, role=role, active=True))
        await session.flush()
        return membership

    return factory


@pytest_asyncio.fixture
async def tenant(
    session: AsyncSession,
    make_organization: Callable[..., Any],
    make_user: Callable[..., Any],
    make_store: Callable[..., Any],
    make_membership: Callable[..., Any],
) -> dict[str, Any]:
    """A committed organization with an owner, one store, and an active session row."""
    from datetime import timedelta

    from app.models import Session as SessionModel
    from app.security import generate_token, hash_token, utc_now

    organization = await make_organization()
    owner = await make_user(phone="+8801700000001", display_name="Owner", pin="1234")
    store = await make_store(organization)
    await make_membership(organization, owner, Role.OWNER, store)

    refresh = generate_token()
    auth_session = SessionModel(
        user_id=owner.id,
        organization_id=organization.id,
        store_id=store.id,
        refresh_token_hash=hash_token(refresh),
        expires_at=utc_now() + timedelta(days=30),
    )
    session.add(auth_session)
    await session.commit()
    return {
        "organization": organization,
        "owner": owner,
        "store": store,
        "session": auth_session,
        "refresh_token": refresh,
    }


def access_token_for(
    *,
    session_id: UUID,
    user_id: UUID,
    organization_id: UUID,
    role: Role = Role.OWNER,
    store_id: UUID | None = None,
    device_id: UUID | None = None,
) -> str:
    """Mint a signed access token matching what ``app.dependencies`` expects."""
    claims: dict[str, Any] = {
        "sid": str(session_id),
        "sub": str(user_id),
        "org": str(organization_id),
        "role": role.value,
    }
    if store_id is not None:
        claims["store"] = str(store_id)
    if device_id is not None:
        claims["dev"] = str(device_id)
    return sign_access_token(claims)


@pytest.fixture
def auth_headers() -> Callable[..., dict[str, str]]:
    def build(tenant: dict[str, Any], *, role: Role = Role.OWNER, with_store: bool = True) -> dict[str, str]:
        token = access_token_for(
            session_id=tenant["session"].id,
            user_id=tenant["owner"].id,
            organization_id=tenant["organization"].id,
            role=role,
            store_id=tenant["store"].id if with_store else None,
        )
        return {"Authorization": f"Bearer {token}"}

    return build

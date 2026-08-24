from __future__ import annotations

from datetime import timedelta
from typing import Any
from uuid import uuid4

from app.models import Role
from app.models import Session as SessionModel
from app.security import generate_token, hash_token, utc_now
from tests.conftest import access_token_for


async def role_headers(session: Any, tenant: dict[str, Any], role: Role) -> dict[str, str]:
    """Mint headers for a real user holding ``role`` in the tenant's organization.

    The context role comes from the live membership row, so testing a capability
    boundary needs an actual member at that role -- a re-signed token for the
    owner would still resolve to the owner's authority.
    """
    from app.models import OrganizationUser, StoreUser, User

    user = User(phone=f"+8801700{uuid4().hex[:7]}", display_name=f"User {role.value}", pin_hash=None)
    session.add(user)
    await session.flush()
    session.add(
        OrganizationUser(organization_id=tenant["organization"].id, user_id=user.id, role=role, active=True)
    )
    session.add(
        StoreUser(store_id=tenant["store"].id, user_id=user.id, role=role, active=True)
    )
    refresh = generate_token()
    auth_session = SessionModel(
        user_id=user.id,
        organization_id=tenant["organization"].id,
        store_id=tenant["store"].id,
        refresh_token_hash=hash_token(refresh),
        expires_at=utc_now() + timedelta(days=30),
    )
    session.add(auth_session)
    await session.commit()
    token = access_token_for(
        session_id=auth_session.id,
        user_id=user.id,
        organization_id=tenant["organization"].id,
        role=role,
        store_id=tenant["store"].id,
    )
    return {"Authorization": f"Bearer {token}"}

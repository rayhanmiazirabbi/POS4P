from __future__ import annotations

import re
from dataclasses import dataclass, replace
from uuid import UUID

from sqlalchemy import Select, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.context import RequestContext
from app.errors import Conflict, Forbidden, NotFound
from app.models import OrganizationUser, RecordStatus, Role, Store, StoreUser, User
from app.models import Session as SessionModel
from app.schemas.users import (
    PinSetRequest,
    StoreAssignmentRequest,
    UserCreateRequest,
    UserRoleUpdateRequest,
    UserStatusUpdateRequest,
    UserUpdateRequest,
)
from app.security import hash_secret, utc_now
from app.services.audit import record_audit, redact
from app.services.organizations import get_organization, require_active_organization
from app.services.stores import load_store

_NON_DIGITS = re.compile(r"\D")
_SEPARATORS = re.compile(r"[\s().-]")


def normalize_phone(value: str) -> str:
    """Canonicalise a Bangladesh mobile number to ``+880`` form.

    Mirrors ``normalizePhone`` in ``@pharmacy/core`` digit for digit: the phone column
    is globally unique, so a client that sends ``01700-000000`` and one that sends
    ``+8801700000000`` must collide instead of creating two accounts for one person.
    """
    compact = _SEPARATORS.sub("", value.strip())
    if compact.startswith("+"):
        return f"+{_NON_DIGITS.sub('', compact[1:])}"
    digits = _NON_DIGITS.sub("", compact)
    return f"+880{digits[1:]}" if digits.startswith("0") else f"+880{digits}"


@dataclass(frozen=True, slots=True)
class StaffProfile:
    """A staff member plus the membership rows that describe their access."""

    user: User
    membership: OrganizationUser
    store_memberships: list[StoreUser]


def _membership_summary(membership: OrganizationUser) -> dict[str, object]:
    return {"role": membership.role.value, "active": membership.active}


async def _store_memberships(
    session: AsyncSession, organization_id: UUID, user_ids: list[UUID]
) -> dict[UUID, list[StoreUser]]:
    """Branch assignments grouped per user, joined through ``stores`` to stay in-tenant."""
    if not user_ids:
        return {}
    rows = await session.scalars(
        select(StoreUser)
        .join(Store, Store.id == StoreUser.store_id)
        .where(Store.organization_id == organization_id, StoreUser.user_id.in_(user_ids))
        .order_by(StoreUser.store_id)
    )
    grouped: dict[UUID, list[StoreUser]] = {user_id: [] for user_id in user_ids}
    for row in rows:
        grouped.setdefault(row.user_id, []).append(row)
    return grouped


async def load_staff(session: AsyncSession, context: RequestContext, user_id: UUID) -> StaffProfile:
    """Fetch a staff member by id under the caller's tenant.

    A user outside the organization -- including one that exists in another tenant --
    is reported as missing rather than forbidden, so direct object probing cannot
    confirm that the id exists. Inactive memberships are still returned so a removed
    member can be inspected and restored.
    """
    membership = await session.scalar(
        select(OrganizationUser).where(
            OrganizationUser.organization_id == context.organization_id,
            OrganizationUser.user_id == user_id,
        )
    )
    if membership is None:
        raise NotFound("User not found")
    user = await session.get(User, user_id)
    if user is None:
        raise NotFound("User not found")
    grouped = await _store_memberships(session, context.organization_id, [user_id])
    return StaffProfile(user, membership, grouped.get(user_id, []))


async def list_staff(
    session: AsyncSession,
    context: RequestContext,
    *,
    role: Role | None = None,
    status: RecordStatus | None = None,
    store_id: UUID | None = None,
) -> list[StaffProfile]:
    """The organization's roster, optionally narrowed to a role, state, or branch."""
    query: Select[tuple[OrganizationUser, User]] = (
        select(OrganizationUser, User)
        .join(User, User.id == OrganizationUser.user_id)
        .where(OrganizationUser.organization_id == context.organization_id)
        .order_by(User.display_name)
    )
    if role is not None:
        query = query.where(OrganizationUser.role == role)
    if status is not None:
        query = query.where(User.status == status)
    if store_id is not None:
        store = await load_store(session, context, store_id)
        query = query.where(
            OrganizationUser.user_id.in_(
                select(StoreUser.user_id).where(
                    StoreUser.store_id == store.id, StoreUser.active.is_(True)
                )
            )
        )

    rows = list(await session.execute(query))
    grouped = await _store_memberships(
        session, context.organization_id, [user.id for _, user in rows]
    )
    return [
        StaffProfile(user, membership, grouped.get(user.id, []))
        for membership, user in rows
    ]


async def _assert_phone_available(
    session: AsyncSession, phone: str, *, exclude: UUID | None = None
) -> None:
    """The uniqueness message never says *where* the number is already in use.

    Confirming that a phone belongs to another tenant would leak that tenant's roster.
    """
    query = select(User.id).where(User.phone == phone)
    if exclude is not None:
        query = query.where(User.id != exclude)
    if await session.scalar(query) is not None:
        raise Conflict("A user with this phone number already exists")


async def _active_owners_besides(
    session: AsyncSession, organization_id: UUID, user_id: UUID
) -> int:
    count = await session.scalar(
        select(func.count())
        .select_from(OrganizationUser)
        .join(User, User.id == OrganizationUser.user_id)
        .where(
            OrganizationUser.organization_id == organization_id,
            OrganizationUser.user_id != user_id,
            OrganizationUser.role == Role.OWNER,
            OrganizationUser.active.is_(True),
            User.status == RecordStatus.ACTIVE,
        )
    )
    return int(count or 0)


async def _protect_last_owner(
    session: AsyncSession, context: RequestContext, profile: StaffProfile
) -> None:
    """Refuse to strip the final active owner.

    An organization with no reachable owner can never be administered again -- not
    even to appoint a successor -- so the replacement has to exist *first*. Only a
    membership that currently counts as an active owner is protected.
    """
    if profile.membership.role is not Role.OWNER or not profile.membership.active:
        return
    if profile.user.status is not RecordStatus.ACTIVE:
        return
    if await _active_owners_besides(session, context.organization_id, profile.user.id) == 0:
        raise Conflict(
            "The organization must keep at least one active owner; "
            "appoint a replacement owner first"
        )


def _assert_may_administer(context: RequestContext, profile: StaffProfile) -> None:
    """Managers hold ``users.manage`` but must not reach above their own rank."""
    if context.role is not Role.OWNER and profile.membership.role is Role.OWNER:
        raise Forbidden("Only an owner may administer another owner")


def _assert_may_grant(context: RequestContext, role: Role) -> None:
    if role is Role.OWNER and context.role is not Role.OWNER:
        raise Forbidden("Only an owner may grant the owner role")


async def _revoke_sessions(session: AsyncSession, organization_id: UUID, user_id: UUID) -> None:
    """End live sessions the moment access is withdrawn.

    ``resolve_context`` already fails closed on an inactive user, but revoking here
    also covers the case where only the membership was removed, and leaves an
    auditable trail that the tokens were killed rather than merely ignored.
    """
    await session.execute(
        update(SessionModel)
        .where(
            SessionModel.user_id == user_id,
            SessionModel.organization_id == organization_id,
            SessionModel.revoked_at.is_(None),
        )
        .values(revoked_at=utc_now())
    )


def _withdraw_store_access(profile: StaffProfile) -> list[UUID]:
    revoked = [row.store_id for row in profile.store_memberships if row.active]
    for row in profile.store_memberships:
        row.active = False
    return revoked


async def create_user(
    session: AsyncSession,
    context: RequestContext,
    payload: UserCreateRequest,
    *,
    request_id: str,
) -> StaffProfile:
    """Create a staff account, its organization membership, and its branch row together.

    A half-applied create would burn the globally unique phone number on a user that
    no organization can see, so every row and the audit entry commit as one.
    """
    require_active_organization(await get_organization(session, context))
    phone = normalize_phone(payload.phone)
    await _assert_phone_available(session, phone)
    role = Role(payload.role)
    store = (
        await load_store(session, context, payload.store_id)
        if payload.store_id is not None
        else None
    )

    user = User(
        phone=phone,
        display_name=payload.display_name.strip(),
        status=RecordStatus.ACTIVE,
        pin_hash=hash_secret(payload.pin) if payload.pin else None,
    )
    session.add(user)
    try:
        await session.flush()
        membership = OrganizationUser(
            organization_id=context.organization_id,
            user_id=user.id,
            role=role,
            active=True,
        )
        session.add(membership)
        store_memberships: list[StoreUser] = []
        if store is not None:
            assignment = StoreUser(store_id=store.id, user_id=user.id, role=role, active=True)
            session.add(assignment)
            store_memberships.append(assignment)
        record_audit(
            session,
            context,
            action="user.created",
            entity_type="user",
            entity_id=user.id,
            request_id=request_id,
            after=redact(
                {
                    "phone": user.phone,
                    "displayName": user.display_name,
                    "role": role.value,
                    "status": user.status.value,
                    "pinSet": bool(payload.pin),
                    "storeIds": [str(store.id)] if store is not None else [],
                }
            ),
        )
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise Conflict("A user with this phone number already exists") from exc
    except Exception:
        await session.rollback()
        raise
    return StaffProfile(user, membership, store_memberships)


async def update_user(
    session: AsyncSession,
    context: RequestContext,
    user_id: UUID,
    payload: UserUpdateRequest,
    *,
    request_id: str,
) -> StaffProfile:
    require_active_organization(await get_organization(session, context))
    profile = await load_staff(session, context, user_id)
    _assert_may_administer(context, profile)
    changes = payload.model_dump(exclude_unset=True, exclude_none=True)
    if not changes:
        return profile

    user = profile.user
    before = {"phone": user.phone, "displayName": user.display_name}
    if "phone" in changes:
        phone = normalize_phone(changes["phone"])
        if phone != user.phone:
            await _assert_phone_available(session, phone, exclude=user.id)
            user.phone = phone
    if "display_name" in changes:
        user.display_name = changes["display_name"].strip()
    record_audit(
        session,
        context,
        action="user.updated",
        entity_type="user",
        entity_id=user.id,
        request_id=request_id,
        before=redact(before),
        after=redact({"phone": user.phone, "displayName": user.display_name}),
    )
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise Conflict("A user with this phone number already exists") from exc
    return profile


async def change_role(
    session: AsyncSession,
    context: RequestContext,
    user_id: UUID,
    payload: UserRoleUpdateRequest,
    *,
    request_id: str,
) -> StaffProfile:
    """Move a member between roles, keeping their branch rows in step.

    Demoting the last active owner is refused, because the demotion would leave the
    organization with nobody able to promote a successor.
    """
    require_active_organization(await get_organization(session, context))
    profile = await load_staff(session, context, user_id)
    target = Role(payload.role)
    _assert_may_administer(context, profile)
    _assert_may_grant(context, target)
    if profile.membership.role is target:
        return profile
    if target is not Role.OWNER:
        await _protect_last_owner(session, context, profile)

    before = _membership_summary(profile.membership)
    profile.membership.role = target
    for row in profile.store_memberships:
        row.role = target
    record_audit(
        session,
        context,
        action="user.role_changed",
        entity_type="user",
        entity_id=profile.user.id,
        request_id=request_id,
        before=redact(before),
        after=redact({"role": target.value, "active": True, "reason": payload.reason}),
    )
    await session.commit()
    return profile


async def change_status(
    session: AsyncSession,
    context: RequestContext,
    user_id: UUID,
    payload: UserStatusUpdateRequest,
    *,
    request_id: str,
) -> StaffProfile:
    """Activate or deactivate a staff account.

    Deactivation is a containment action, so it stays available even while the tenant
    is suspended. Reactivation restores organization membership only -- branch
    assignments are re-granted explicitly, so a returning member starts with the
    narrowest access.
    """
    profile = await load_staff(session, context, user_id)
    _assert_may_administer(context, profile)
    target = RecordStatus(payload.status)
    before = {"status": profile.user.status.value, **_membership_summary(profile.membership)}
    if target is profile.user.status and profile.membership.active is (
        target is RecordStatus.ACTIVE
    ):
        return profile
    if target is not RecordStatus.ACTIVE:
        await _protect_last_owner(session, context, profile)

    profile.user.status = target
    profile.membership.active = target is RecordStatus.ACTIVE
    revoked: list[UUID] = []
    if target is not RecordStatus.ACTIVE:
        revoked = _withdraw_store_access(profile)
        await _revoke_sessions(session, context.organization_id, profile.user.id)
    record_audit(
        session,
        context,
        action="user.status_changed",
        entity_type="user",
        entity_id=profile.user.id,
        request_id=request_id,
        before=redact(before),
        after=redact(
            {
                "status": target.value,
                "active": profile.membership.active,
                "revokedStoreIds": [str(store_id) for store_id in revoked],
                "reason": payload.reason,
            }
        ),
    )
    await session.commit()
    return profile


async def remove_membership(
    session: AsyncSession,
    context: RequestContext,
    user_id: UUID,
    *,
    request_id: str,
) -> StaffProfile:
    """Withdraw organization access while keeping the user row and its audit history.

    Deactivating rather than deleting preserves the foreign keys that sales, audit,
    and stock movements hold on the actor.
    """
    profile = await load_staff(session, context, user_id)
    _assert_may_administer(context, profile)
    if not profile.membership.active:
        return profile
    await _protect_last_owner(session, context, profile)

    before = _membership_summary(profile.membership)
    profile.membership.active = False
    revoked = _withdraw_store_access(profile)
    await _revoke_sessions(session, context.organization_id, profile.user.id)
    record_audit(
        session,
        context,
        action="user.membership_removed",
        entity_type="user",
        entity_id=profile.user.id,
        request_id=request_id,
        before=redact(before),
        after=redact(
            {
                "role": profile.membership.role.value,
                "active": False,
                "revokedStoreIds": [str(store_id) for store_id in revoked],
            }
        ),
    )
    await session.commit()
    return profile


async def set_pin(
    session: AsyncSession,
    context: RequestContext,
    user_id: UUID,
    payload: PinSetRequest,
    *,
    request_id: str,
) -> StaffProfile:
    """Set or reset a staff PIN on the member's behalf.

    Only the Argon2id digest is stored, and the audit trail records that a reset
    happened -- never the PIN or its hash.
    """
    require_active_organization(await get_organization(session, context))
    profile = await load_staff(session, context, user_id)
    _assert_may_administer(context, profile)
    if not profile.membership.active or profile.user.status is not RecordStatus.ACTIVE:
        raise Conflict("User is not an active member of this organization")

    had_pin = profile.user.pin_hash is not None
    profile.user.pin_hash = hash_secret(payload.pin)
    record_audit(
        session,
        context,
        action="user.pin_reset",
        entity_type="user",
        entity_id=profile.user.id,
        request_id=request_id,
        before=redact({"pinSet": had_pin}),
        after=redact({"pinSet": True}),
    )
    await session.commit()
    return profile


async def clear_pin(
    session: AsyncSession,
    context: RequestContext,
    user_id: UUID,
    *,
    request_id: str,
) -> StaffProfile:
    """Drop a PIN so it cannot be used until an administrator issues a new one."""
    profile = await load_staff(session, context, user_id)
    _assert_may_administer(context, profile)
    if profile.user.pin_hash is None:
        return profile
    profile.user.pin_hash = None
    record_audit(
        session,
        context,
        action="user.pin_cleared",
        entity_type="user",
        entity_id=profile.user.id,
        request_id=request_id,
        before=redact({"pinSet": True}),
        after=redact({"pinSet": False}),
    )
    await session.commit()
    return profile


async def assign_store(
    session: AsyncSession,
    context: RequestContext,
    user_id: UUID,
    payload: StoreAssignmentRequest,
    *,
    request_id: str,
) -> StaffProfile:
    """Grant a member access to a branch.

    ``store_users`` is unique on (store, user), so a concurrent duplicate insert loses
    the race at the database and is reported as a conflict rather than a server error.
    """
    require_active_organization(await get_organization(session, context))
    profile = await load_staff(session, context, user_id)
    _assert_may_administer(context, profile)
    if not profile.membership.active or profile.user.status is not RecordStatus.ACTIVE:
        raise Conflict("User is not an active member of this organization")
    store = await load_store(session, context, payload.store_id)

    existing = next(
        (row for row in profile.store_memberships if row.store_id == store.id), None
    )
    if existing is not None and existing.active:
        raise Conflict("User is already assigned to this store")

    role = profile.membership.role
    if existing is not None:
        existing.active = True
        existing.role = role
        assignment = existing
        memberships = profile.store_memberships
    else:
        assignment = StoreUser(store_id=store.id, user_id=profile.user.id, role=role, active=True)
        session.add(assignment)
        memberships = [*profile.store_memberships, assignment]
    record_audit(
        session,
        replace(context, store_id=store.id),
        action="user.store_assigned",
        entity_type="user",
        entity_id=profile.user.id,
        request_id=request_id,
        after=redact({"storeId": str(store.id), "role": role.value, "active": True}),
    )
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise Conflict("User is already assigned to this store") from exc
    return StaffProfile(profile.user, profile.membership, memberships)


async def unassign_store(
    session: AsyncSession,
    context: RequestContext,
    user_id: UUID,
    store_id: UUID,
    *,
    request_id: str,
) -> StaffProfile:
    profile = await load_staff(session, context, user_id)
    _assert_may_administer(context, profile)
    store = await load_store(session, context, store_id)
    assignment = next(
        (row for row in profile.store_memberships if row.store_id == store.id), None
    )
    if assignment is None or not assignment.active:
        raise NotFound("Store assignment not found")

    assignment.active = False
    record_audit(
        session,
        replace(context, store_id=store.id),
        action="user.store_unassigned",
        entity_type="user",
        entity_id=profile.user.id,
        request_id=request_id,
        before=redact({"storeId": str(store.id), "role": assignment.role.value, "active": True}),
        after=redact({"storeId": str(store.id), "active": False}),
    )
    await session.commit()
    return profile

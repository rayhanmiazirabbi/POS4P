from __future__ import annotations

from datetime import timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.context import RequestContext
from app.errors import Forbidden, NotFound, RateLimited, Unauthorized, ValidationError
from app.models import (
    Device,
    DeviceStatus,
    Organization,
    OrganizationUser,
    RecordStatus,
    Role,
    Store,
    StoreUser,
    User,
)
from app.models import Session as SessionModel
from app.schemas.auth import (
    DeviceClaim,
    MembershipOption,
    OtpRequest,
    OtpVerifyRequest,
    PinLoginRequest,
    SelectContextRequest,
)
from app.security import (
    as_utc,
    generate_otp,
    generate_token,
    hash_secret,
    hash_token,
    sign_access_token,
    utc_now,
    verify_secret,
)
from app.services.audit import record_audit, redact
from app.services.users import normalize_phone

#: Argon2 digest of a value no caller can produce, verified against when the phone is
#: unknown so a missing account costs the same wall-clock time as a wrong PIN.
_DUMMY_HASH = hash_secret("::unresolvable-account::")

#: Roles that reach every store in the tenant without an explicit ``store_users`` row.
_STORE_WIDE_ROLES = frozenset({Role.OWNER, Role.MANAGER})

#: Returned for every failed staff login regardless of cause. Distinguishing "no such
#: phone" from "wrong PIN" would turn the endpoint into an account oracle.
_LOGIN_FAILED = "Phone number or PIN is incorrect"


class AuthResult:
    """The credential pair plus the context it was minted for."""

    __slots__ = (
        "access_token",
        "expires_in",
        "memberships",
        "refresh_token",
        "role",
        "session",
        "user",
    )

    def __init__(
        self,
        *,
        access_token: str,
        refresh_token: str,
        session: SessionModel,
        user: User,
        role: Role | None,
        memberships: list[MembershipOption],
        expires_in: int,
    ) -> None:
        self.access_token = access_token
        self.refresh_token = refresh_token
        self.session = session
        self.user = user
        self.role = role
        self.memberships = memberships
        self.expires_in = expires_in


# --- shared helpers --------------------------------------------------------


async def _active_membership(
    session: AsyncSession, organization_id: UUID, user_id: UUID
) -> OrganizationUser | None:
    return await session.scalar(
        select(OrganizationUser).where(
            OrganizationUser.organization_id == organization_id,
            OrganizationUser.user_id == user_id,
            OrganizationUser.active.is_(True),
        )
    )


async def _membership_rows(
    session: AsyncSession, user: User
) -> list[tuple[OrganizationUser, Organization, list[UUID]]]:
    """Every tenant the user may enter, with the stores reachable in each.

    Returns ORM rows rather than the response schema because ``ApiModel`` serializes
    enums to their values; the caller still needs the ``Role`` member to sign a token.
    """
    rows = (
        await session.execute(
            select(OrganizationUser, Organization)
            .join(Organization, Organization.id == OrganizationUser.organization_id)
            .where(
                OrganizationUser.user_id == user.id,
                OrganizationUser.active.is_(True),
                Organization.status == RecordStatus.ACTIVE,
            )
            .order_by(Organization.name)
        )
    ).all()
    return [
        (
            membership,
            organization,
            await _accessible_store_ids(session, organization.id, user.id, membership.role),
        )
        for membership, organization in rows
    ]


def _to_options(
    rows: list[tuple[OrganizationUser, Organization, list[UUID]]],
) -> list[MembershipOption]:
    """This is the only place membership is disclosed, and only to the user it describes."""
    return [
        MembershipOption(
            organization_id=organization.id,
            organization_name=organization.name,
            role=membership.role,
            store_ids=store_ids,
        )
        for membership, organization, store_ids in rows
    ]


async def _membership_options(session: AsyncSession, user: User) -> list[MembershipOption]:
    return _to_options(await _membership_rows(session, user))


async def _accessible_store_ids(
    session: AsyncSession, organization_id: UUID, user_id: UUID, role: Role
) -> list[UUID]:
    query = select(Store.id).where(
        Store.organization_id == organization_id, Store.status == RecordStatus.ACTIVE
    )
    if role not in _STORE_WIDE_ROLES:
        query = query.join(StoreUser, StoreUser.store_id == Store.id).where(
            StoreUser.user_id == user_id, StoreUser.active.is_(True)
        )
    return list(await session.scalars(query))


async def _resolve_store(
    session: AsyncSession, organization_id: UUID, user_id: UUID, role: Role, store_id: UUID | None
) -> Store | None:
    """Validate a requested store against the tenant *and* the caller's store access.

    A store outside the organization is reported as missing rather than forbidden so
    the response cannot be used to confirm that an id exists in another tenant.
    """
    if store_id is None:
        return None
    store = await session.scalar(
        select(Store).where(Store.id == store_id, Store.organization_id == organization_id)
    )
    if store is None:
        raise NotFound("Store not found")
    if store.status is not RecordStatus.ACTIVE:
        raise Forbidden(f"Store is {store.status.value}")
    if role not in _STORE_WIDE_ROLES:
        assignment = await session.scalar(
            select(StoreUser.id).where(
                StoreUser.store_id == store.id,
                StoreUser.user_id == user_id,
                StoreUser.active.is_(True),
            )
        )
        if assignment is None:
            raise Forbidden("Store access denied")
    return store


async def _resolve_device(
    session: AsyncSession,
    claim: DeviceClaim | None,
    *,
    organization_id: UUID,
    store: Store | None,
) -> Device | None:
    """Bind a login to a device row, registering it on first sight.

    A revoked device is rejected before any session is issued, so wiping a stolen
    tablet takes effect on its next login attempt as well as its next request.
    """
    if claim is None:
        return None
    if store is None:
        raise ValidationError(
            "Store context required to authorize a device", code="STORE_CONTEXT_REQUIRED"
        )
    device = await session.scalar(
        select(Device).where(
            Device.organization_id == organization_id, Device.device_key == claim.device_key
        )
    )
    if device is None:
        device = Device(
            organization_id=organization_id,
            store_id=store.id,
            device_key=claim.device_key,
            name=claim.device_name,
            status=DeviceStatus.ACTIVE,
            last_seen_at=utc_now(),
        )
        session.add(device)
        await session.flush()
        return device
    if device.status is not DeviceStatus.ACTIVE:
        raise Forbidden("Device authorization has been revoked")
    device.store_id = store.id
    device.last_seen_at = utc_now()
    return device


def _access_token(
    *,
    session_row: SessionModel,
    user: User,
    role: Role | None,
    organization_id: UUID | None,
    store_id: UUID | None,
    device_id: UUID | None,
) -> str:
    claims: dict[str, Any] = {"sid": str(session_row.id), "sub": str(user.id)}
    if organization_id is not None:
        claims["org"] = str(organization_id)
    if role is not None:
        claims["role"] = role.value
    if store_id is not None:
        claims["store"] = str(store_id)
    if device_id is not None:
        claims["dev"] = str(device_id)
    return sign_access_token(claims)


async def _issue_session(
    session: AsyncSession,
    user: User,
    *,
    organization_id: UUID | None,
    store_id: UUID | None,
    role: Role | None,
    device_id: UUID | None,
) -> tuple[SessionModel, str, str]:
    settings = get_settings()
    refresh = generate_token()
    row = SessionModel(
        user_id=user.id,
        organization_id=organization_id,
        store_id=store_id,
        refresh_token_hash=hash_token(refresh),
        device_id=device_id,
        expires_at=utc_now() + timedelta(days=settings.refresh_token_days),
    )
    session.add(row)
    await session.flush()
    access = _access_token(
        session_row=row,
        user=user,
        role=role,
        organization_id=organization_id,
        store_id=store_id,
        device_id=device_id,
    )
    return row, access, refresh


def _result(
    *,
    access: str,
    refresh: str,
    row: SessionModel,
    user: User,
    role: Role | None = None,
    memberships: list[MembershipOption],
) -> AuthResult:
    return AuthResult(
        access_token=access,
        refresh_token=refresh,
        session=row,
        user=user,
        role=role,
        memberships=memberships,
        expires_in=get_settings().access_token_minutes * 60,
    )


# --- OTP -------------------------------------------------------------------


async def request_otp(
    session: AsyncSession, payload: OtpRequest, *, request_id: str
) -> tuple[UUID, Any, str | None]:
    """Issue a one-time code for a phone number.

    The response is identical whether or not an account exists, so the endpoint
    cannot be used to enumerate registered numbers. The code is stored only as an
    Argon2id digest; nothing recoverable is written to the database or the log.
    """
    settings = get_settings()
    destination = normalize_phone(payload.phone)
    window_start = utc_now() - timedelta(seconds=settings.otp_request_window_seconds)

    from app.models import AuthChallenge

    recent = await session.scalar(
        select(func.count())
        .select_from(AuthChallenge)
        .where(AuthChallenge.destination == destination, AuthChallenge.created_at >= window_start)
    )
    if (recent or 0) >= settings.otp_max_requests_per_window:
        raise RateLimited("Too many verification codes requested. Try again later.")

    code = generate_otp()
    challenge = AuthChallenge(
        destination=destination,
        purpose=payload.purpose,
        challenge_hash=hash_secret(code),
        expires_at=utc_now() + timedelta(seconds=settings.otp_expiry_seconds),
        attempts=0,
        created_at=utc_now(),
    )
    session.add(challenge)
    await session.commit()

    # Without an SMS provider wired up there is no other way to complete a login in
    # development. Production must never disclose the code over the API.
    dev_code = None if settings.environment == "production" else code
    return challenge.id, challenge.expires_at, dev_code


async def verify_otp(
    session: AsyncSession, payload: OtpVerifyRequest, *, request_id: str
) -> AuthResult:
    """Consume a challenge and mint a session.

    A consumed, expired, or exhausted challenge is rejected before the code is even
    compared, which closes replay and makes brute force cost one challenge per
    ``otp_max_attempts`` guesses.
    """
    from app.models import AuthChallenge

    settings = get_settings()
    challenge = await session.get(AuthChallenge, payload.challenge_id)
    if challenge is None:
        raise Unauthorized("Verification code is invalid or expired")
    if challenge.consumed_at is not None:
        raise Unauthorized("Verification code has already been used")
    expires_at = as_utc(challenge.expires_at)
    if expires_at is not None and expires_at <= utc_now():
        raise Unauthorized("Verification code is invalid or expired")
    if challenge.attempts >= settings.otp_max_attempts:
        raise RateLimited("Too many incorrect attempts. Request a new code.")

    if not verify_secret(challenge.challenge_hash, payload.code):
        challenge.attempts += 1
        await session.commit()
        raise Unauthorized("Verification code is invalid or expired")

    challenge.consumed_at = utc_now()

    user = await session.scalar(select(User).where(User.phone == challenge.destination))
    if user is None:
        user = User(
            phone=challenge.destination,
            display_name=(payload.display_name or challenge.destination).strip(),
            status=RecordStatus.ACTIVE,
        )
        session.add(user)
        try:
            await session.flush()
        except IntegrityError:
            # Two codes for one number verified concurrently; the row the other
            # request created is the one to keep.
            await session.rollback()
            raise Unauthorized("Verification could not be completed. Try again.") from None
    elif user.status is not RecordStatus.ACTIVE:
        raise Forbidden("User account is not active")

    rows = await _membership_rows(session, user)
    memberships = _to_options(rows)
    organization_id: UUID | None = None
    role: Role | None = None
    store: Store | None = None
    if len(rows) == 1:
        # Exactly one tenant (and one store in it) means there is nothing to choose,
        # so the session is scoped straight away and the client skips a round trip.
        membership, organization, store_ids = rows[0]
        organization_id, role = organization.id, membership.role
        if len(store_ids) == 1:
            store = await session.get(Store, store_ids[0])

    device = (
        await _resolve_device(
            session, payload.device, organization_id=organization_id, store=store
        )
        if organization_id is not None and store is not None
        else None
    )

    row, access, refresh = await _issue_session(
        session,
        user,
        organization_id=organization_id,
        store_id=store.id if store else None,
        role=role,
        device_id=device.id if device else None,
    )
    # An audit row is tenant-scoped, so a login that has not selected an organization
    # yet has nowhere to record itself; ``select_context`` audits that step instead.
    if organization_id is not None and role is not None:
        record_audit(
            session,
            RequestContext(
                organization_id=organization_id,
                user_id=user.id,
                role=role,
                store_id=store.id if store else None,
                device_id=device.id if device else None,
            ),
            action="auth.otp_login",
            entity_type="session",
            entity_id=row.id,
            request_id=request_id,
            after=redact({"method": "otp", "purpose": challenge.purpose}),
        )
    await session.commit()
    return _result(
        access=access, refresh=refresh, row=row, user=user, role=role, memberships=memberships
    )


# --- staff PIN -------------------------------------------------------------


async def login_with_pin(
    session: AsyncSession, payload: PinLoginRequest, *, request_id: str
) -> AuthResult:
    """Authenticate a staff member by phone + PIN within one tenant.

    Every failure path returns the same message and the same shape. Repeated
    failures lock the account for ``pin_lockout_seconds``, which bounds an online
    guessing attack against a four-digit secret.
    """
    settings = get_settings()
    phone = normalize_phone(payload.phone)
    user = await session.scalar(select(User).where(User.phone == phone))

    if user is None:
        # Burn comparable time so response latency does not reveal the account.
        verify_secret(_DUMMY_HASH, payload.pin)
        raise Unauthorized(_LOGIN_FAILED)

    locked_until = as_utc(user.pin_locked_until)
    if locked_until is not None and locked_until > utc_now():
        raise RateLimited("Too many incorrect attempts. Try again later.")

    membership = await _active_membership(session, payload.organization_id, user.id)
    if user.status is not RecordStatus.ACTIVE or membership is None:
        verify_secret(_DUMMY_HASH, payload.pin)
        raise Unauthorized(_LOGIN_FAILED)

    if not verify_secret(user.pin_hash, payload.pin):
        user.pin_attempts += 1
        if user.pin_attempts >= settings.pin_max_attempts:
            user.pin_locked_until = utc_now() + timedelta(seconds=settings.pin_lockout_seconds)
            user.pin_attempts = 0
        await session.commit()
        raise Unauthorized(_LOGIN_FAILED)

    user.pin_attempts = 0
    user.pin_locked_until = None

    store = await _resolve_store(
        session, payload.organization_id, user.id, membership.role, payload.store_id
    )
    device = await _resolve_device(
        session, payload.device, organization_id=payload.organization_id, store=store
    )
    row, access, refresh = await _issue_session(
        session,
        user,
        organization_id=payload.organization_id,
        store_id=store.id if store else None,
        role=membership.role,
        device_id=device.id if device else None,
    )
    record_audit(
        session,
        RequestContext(
            organization_id=payload.organization_id,
            user_id=user.id,
            role=membership.role,
            store_id=store.id if store else None,
            device_id=device.id if device else None,
        ),
        action="auth.pin_login",
        entity_type="session",
        entity_id=row.id,
        request_id=request_id,
        after=redact({"method": "pin", "storeId": str(store.id) if store else None}),
    )
    await session.commit()
    return _result(
        access=access, refresh=refresh, row=row, user=user, role=membership.role, memberships=[]
    )


# --- session lifecycle -----------------------------------------------------


async def refresh_session(
    session: AsyncSession, refresh_token: str, *, request_id: str
) -> AuthResult:
    """Rotate a refresh token, re-checking that the account may still act.

    Presenting a token that was already rotated away means it leaked, so the whole
    session is revoked rather than refreshed -- the legitimate holder is logged out
    and has to re-authenticate, which is the safe outcome.
    """
    presented = hash_token(refresh_token)
    row = await session.scalar(
        select(SessionModel).where(SessionModel.refresh_token_hash == presented)
    )
    if row is None:
        replayed = await session.scalar(
            select(SessionModel).where(SessionModel.rotated_from_hash == presented)
        )
        if replayed is not None and replayed.revoked_at is None:
            replayed.revoked_at = utc_now()
            await session.commit()
            raise Unauthorized("Refresh token was already used; the session has been revoked")
        raise Unauthorized("Refresh token is invalid")

    if row.revoked_at is not None:
        raise Unauthorized("Session has been revoked")
    expires_at = as_utc(row.expires_at)
    if expires_at is not None and expires_at <= utc_now():
        raise Unauthorized("Session has expired")

    user = await session.get(User, row.user_id)
    if user is None or user.status is not RecordStatus.ACTIVE:
        raise Unauthorized("User account is not active")

    role: Role | None = None
    if row.organization_id is not None:
        membership = await _active_membership(session, row.organization_id, user.id)
        if membership is None:
            raise Forbidden("Organization access denied")
        role = membership.role

    if row.device_id is not None:
        device = await session.get(Device, row.device_id)
        if device is None or device.status is not DeviceStatus.ACTIVE:
            row.revoked_at = utc_now()
            await session.commit()
            raise Forbidden("Device authorization has been revoked")

    rotated = generate_token()
    row.rotated_from_hash = row.refresh_token_hash
    row.refresh_token_hash = hash_token(rotated)
    row.expires_at = utc_now() + timedelta(days=get_settings().refresh_token_days)
    access = _access_token(
        session_row=row,
        user=user,
        role=role,
        organization_id=row.organization_id,
        store_id=row.store_id,
        device_id=row.device_id,
    )
    await session.commit()
    return _result(
        access=access, refresh=rotated, row=row, user=user, role=role, memberships=[]
    )


async def select_context(
    session: AsyncSession, user: User, payload: SelectContextRequest, *, request_id: str
) -> AuthResult:
    """Re-scope the caller's credentials onto a chosen organization and store.

    The membership is re-read here rather than trusted from the previous token, so
    selecting a tenant the caller was removed from fails even if it was offered a
    moment earlier.
    """
    membership = await _active_membership(session, payload.organization_id, user.id)
    if membership is None:
        raise Forbidden("Organization access denied")
    organization = await session.get(Organization, payload.organization_id)
    if organization is None:
        raise NotFound("Organization not found")

    store = await _resolve_store(
        session, payload.organization_id, user.id, membership.role, payload.store_id
    )
    device = await _resolve_device(
        session, payload.device, organization_id=payload.organization_id, store=store
    )
    row, access, refresh = await _issue_session(
        session,
        user,
        organization_id=payload.organization_id,
        store_id=store.id if store else None,
        role=membership.role,
        device_id=device.id if device else None,
    )
    record_audit(
        session,
        RequestContext(
            organization_id=payload.organization_id,
            user_id=user.id,
            role=membership.role,
            store_id=store.id if store else None,
            device_id=device.id if device else None,
        ),
        action="auth.context_selected",
        entity_type="session",
        entity_id=row.id,
        request_id=request_id,
        after=redact({"storeId": str(store.id) if store else None}),
    )
    await session.commit()
    return _result(
        access=access,
        refresh=refresh,
        row=row,
        user=user,
        role=membership.role,
        memberships=await _membership_options(session, user),
    )


async def logout(
    session: AsyncSession, context: RequestContext, *, session_id: UUID, request_id: str
) -> list[UUID]:
    row = await session.get(SessionModel, session_id)
    if row is None or row.user_id != context.user_id:
        raise NotFound("Session not found")
    revoked: list[UUID] = []
    if row.revoked_at is None:
        row.revoked_at = utc_now()
        revoked.append(row.id)
    record_audit(
        session,
        context,
        action="auth.logout",
        entity_type="session",
        entity_id=row.id,
        request_id=request_id,
    )
    await session.commit()
    return revoked


async def list_sessions(
    session: AsyncSession, context: RequestContext, *, user_id: UUID | None = None
) -> list[SessionModel]:
    """Active sessions for the caller, or for another member when an owner asks.

    Scoped to the tenant in the context, so an owner cannot enumerate sessions that
    belong to an organization they are not currently acting for.
    """
    target = context.user_id if user_id is None else user_id
    if target != context.user_id and context.role not in _STORE_WIDE_ROLES:
        raise Forbidden("Capability denied")
    return list(
        await session.scalars(
            select(SessionModel)
            .where(
                SessionModel.user_id == target,
                SessionModel.organization_id == context.organization_id,
                SessionModel.revoked_at.is_(None),
            )
            .order_by(SessionModel.created_at.desc())
        )
    )


async def revoke_session(
    session: AsyncSession, context: RequestContext, *, session_id: UUID, request_id: str
) -> SessionModel:
    """Revoke one session. Owners and managers may revoke a colleague's.

    A session in another tenant is reported as missing so the endpoint cannot
    confirm that an id exists elsewhere.
    """
    row = await session.scalar(
        select(SessionModel).where(
            SessionModel.id == session_id,
            SessionModel.organization_id == context.organization_id,
        )
    )
    if row is None:
        raise NotFound("Session not found")
    if row.user_id != context.user_id and context.role not in _STORE_WIDE_ROLES:
        raise Forbidden("Capability denied")
    if row.revoked_at is None:
        row.revoked_at = utc_now()
    record_audit(
        session,
        context,
        action="auth.session_revoked",
        entity_type="session",
        entity_id=row.id,
        request_id=request_id,
        after=redact({"targetUserId": str(row.user_id)}),
    )
    await session.commit()
    return row


# --- devices ---------------------------------------------------------------


async def register_device(
    session: AsyncSession, context: RequestContext, *, device_key: str, name: str, request_id: str
) -> Device:
    if context.store_id is None:
        raise ValidationError("Store context required", code="STORE_CONTEXT_REQUIRED")
    existing = await session.scalar(
        select(Device).where(
            Device.organization_id == context.organization_id, Device.device_key == device_key
        )
    )
    if existing is not None:
        # Re-registration is how a revoked terminal is brought back, so it is an
        # explicit privileged action rather than a silent side effect of logging in.
        existing.name = name
        existing.store_id = context.store_id
        existing.status = DeviceStatus.ACTIVE
        existing.last_seen_at = utc_now()
        device = existing
    else:
        device = Device(
            organization_id=context.organization_id,
            store_id=context.store_id,
            device_key=device_key,
            name=name,
            status=DeviceStatus.ACTIVE,
            last_seen_at=utc_now(),
        )
        session.add(device)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise ValidationError("Device key is already registered") from exc
    record_audit(
        session,
        context,
        action="auth.device_registered",
        entity_type="device",
        entity_id=device.id,
        request_id=request_id,
        after=redact({"name": name, "storeId": str(context.store_id)}),
    )
    await session.commit()
    return device


async def list_devices(session: AsyncSession, context: RequestContext) -> list[Device]:
    return list(
        await session.scalars(
            select(Device)
            .where(Device.organization_id == context.organization_id)
            .order_by(Device.created_at.desc())
        )
    )


async def revoke_device(
    session: AsyncSession, context: RequestContext, *, device_id: UUID, request_id: str
) -> Device:
    """Revoke a device and cut every session bound to it.

    Marking the device alone would leave already-issued access tokens working until
    they expired, so the sessions are revoked in the same transaction.
    """
    device = await session.scalar(
        select(Device).where(
            Device.id == device_id, Device.organization_id == context.organization_id
        )
    )
    if device is None:
        raise NotFound("Device not found")
    device.status = DeviceStatus.REVOKED
    bound = await session.scalars(
        select(SessionModel).where(
            SessionModel.device_id == device.id, SessionModel.revoked_at.is_(None)
        )
    )
    now = utc_now()
    for row in bound:
        row.revoked_at = now
    record_audit(
        session,
        context,
        action="auth.device_revoked",
        entity_type="device",
        entity_id=device.id,
        request_id=request_id,
        after=redact({"deviceKey": device.device_key}),
    )
    await session.commit()
    return device

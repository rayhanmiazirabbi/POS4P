from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status

from app.context import RequestContext
from app.dependencies import ContextDep, RequestIdDep, SessionDep, require_roles
from app.errors import Unauthorized
from app.models import Organization, Role, Store, User
from app.models import Session as SessionModel
from app.schemas.auth import (
    CurrentUserResponse,
    DeviceRegisterRequest,
    DeviceResponse,
    LogoutResponse,
    OtpChallengeResponse,
    OtpRequest,
    OtpVerifyRequest,
    PinLoginRequest,
    RefreshRequest,
    SelectContextRequest,
    SessionResponse,
    TokenResponse,
)
from app.schemas.base import Envelope
from app.schemas.users import UserResponse
from app.services import auth as service
from app.services.organizations import ActorDep

router = APIRouter(prefix="/auth", tags=["Authentication"])

#: Authorizing or wiping a shared terminal is an administrative act.
AdminDep = Annotated[RequestContext, Depends(require_roles(Role.OWNER, Role.MANAGER))]


def _token_response(result: service.AuthResult) -> TokenResponse:
    row = result.session
    return TokenResponse(
        access_token=result.access_token,
        refresh_token=result.refresh_token,
        expires_in=result.expires_in,
        session_id=row.id,
        user=UserResponse.model_validate(result.user),
        organization_id=row.organization_id,
        store_id=row.store_id,
        role=result.role,
        requires_organization=row.organization_id is None,
        organizations=result.memberships,
    )


def _session_response(row: SessionModel, *, current_id: UUID | None = None) -> SessionResponse:
    return SessionResponse(
        id=row.id,
        organization_id=row.organization_id,
        store_id=row.store_id,
        device_id=row.device_id,
        expires_at=row.expires_at,
        created_at=row.created_at,
        revoked_at=row.revoked_at,
        current=row.id == current_id,
    )


@router.post(
    "/otp/request",
    response_model=Envelope[OtpChallengeResponse],
    summary="Send a one-time login code",
)
async def request_otp(
    payload: OtpRequest, session: SessionDep, request_id: RequestIdDep
) -> Envelope[OtpChallengeResponse]:
    challenge_id, expires_at, dev_code = await service.request_otp(
        session, payload, request_id=request_id
    )
    return Envelope(
        data=OtpChallengeResponse(
            challenge_id=challenge_id, expires_at=expires_at, dev_code=dev_code
        ),
        request_id=request_id,
    )


@router.post(
    "/otp/verify",
    response_model=Envelope[TokenResponse],
    summary="Exchange a one-time code for a session",
)
async def verify_otp(
    payload: OtpVerifyRequest, session: SessionDep, request_id: RequestIdDep
) -> Envelope[TokenResponse]:
    result = await service.verify_otp(session, payload, request_id=request_id)
    return Envelope(data=_token_response(result), request_id=request_id)


@router.post(
    "/pin/login",
    response_model=Envelope[TokenResponse],
    summary="Authenticate a staff member with their PIN",
)
async def login_with_pin(
    payload: PinLoginRequest, session: SessionDep, request_id: RequestIdDep
) -> Envelope[TokenResponse]:
    result = await service.login_with_pin(session, payload, request_id=request_id)
    return Envelope(data=_token_response(result), request_id=request_id)


@router.post("/refresh", response_model=Envelope[TokenResponse], summary="Rotate a refresh token")
async def refresh(
    payload: RefreshRequest, session: SessionDep, request_id: RequestIdDep
) -> Envelope[TokenResponse]:
    result = await service.refresh_session(session, payload.refresh_token, request_id=request_id)
    return Envelope(data=_token_response(result), request_id=request_id)


@router.post(
    "/context",
    response_model=Envelope[TokenResponse],
    summary="Re-scope credentials onto an organization and store",
)
async def select_context(
    payload: SelectContextRequest,
    session: SessionDep,
    actor: ActorDep,
    request_id: RequestIdDep,
) -> Envelope[TokenResponse]:
    result = await service.select_context(session, actor, payload, request_id=request_id)
    return Envelope(data=_token_response(result), request_id=request_id)


@router.get("/me", response_model=Envelope[CurrentUserResponse], summary="Describe the caller")
async def read_me(
    session: SessionDep, context: ContextDep, request_id: RequestIdDep
) -> Envelope[CurrentUserResponse]:
    """Reports the *live* role and status, not what the token claimed when it was
    minted, so a demotion or store reassignment shows up on the next poll."""
    if context.session_id is None:
        raise Unauthorized("Access token is malformed")
    user = await session.get(User, context.user_id)
    organization = await session.get(Organization, context.organization_id)
    auth_session = await session.get(SessionModel, context.session_id)
    if user is None or organization is None or auth_session is None:
        raise Unauthorized("Authentication required")
    store = await session.get(Store, context.store_id) if context.store_id else None
    return Envelope(
        data=CurrentUserResponse(
            user=UserResponse.model_validate(user),
            organization_id=organization.id,
            organization_name=organization.name,
            role=context.role,
            store_id=store.id if store else None,
            store_name=store.name if store else None,
            device_id=context.device_id,
            pin_set=user.pin_hash is not None,
            session_id=auth_session.id,
            session_expires_at=auth_session.expires_at,
        ),
        request_id=request_id,
    )


@router.get(
    "/sessions",
    response_model=Envelope[list[SessionResponse]],
    summary="List active sessions",
)
async def list_sessions(
    session: SessionDep,
    context: ContextDep,
    request_id: RequestIdDep,
    user_id: Annotated[UUID | None, Query()] = None,
) -> Envelope[list[SessionResponse]]:
    rows = await service.list_sessions(session, context, user_id=user_id)
    return Envelope(
        data=[_session_response(row, current_id=context.session_id) for row in rows],
        request_id=request_id,
    )


@router.post("/logout", response_model=Envelope[LogoutResponse], summary="End the current session")
async def logout(
    session: SessionDep, context: ContextDep, request_id: RequestIdDep
) -> Envelope[LogoutResponse]:
    if context.session_id is None:
        raise Unauthorized("Access token is malformed")
    revoked = await service.logout(
        session, context, session_id=context.session_id, request_id=request_id
    )
    return Envelope(data=LogoutResponse(revoked_session_ids=revoked), request_id=request_id)


@router.post(
    "/sessions/{session_id}/revoke",
    response_model=Envelope[SessionResponse],
    summary="Revoke a session",
)
async def revoke_session(
    session_id: UUID, session: SessionDep, context: ContextDep, request_id: RequestIdDep
) -> Envelope[SessionResponse]:
    row = await service.revoke_session(
        session, context, session_id=session_id, request_id=request_id
    )
    return Envelope(
        data=_session_response(row, current_id=context.session_id), request_id=request_id
    )


@router.post(
    "/devices",
    status_code=status.HTTP_201_CREATED,
    response_model=Envelope[DeviceResponse],
    summary="Authorize a device for the current store",
)
async def register_device(
    payload: DeviceRegisterRequest,
    session: SessionDep,
    context: AdminDep,
    request_id: RequestIdDep,
) -> Envelope[DeviceResponse]:
    device = await service.register_device(
        session, context, device_key=payload.device_key, name=payload.name, request_id=request_id
    )
    return Envelope(data=DeviceResponse.model_validate(device), request_id=request_id)


@router.get(
    "/devices", response_model=Envelope[list[DeviceResponse]], summary="List authorized devices"
)
async def list_devices(
    session: SessionDep, context: ContextDep, request_id: RequestIdDep
) -> Envelope[list[DeviceResponse]]:
    devices = await service.list_devices(session, context)
    return Envelope(
        data=[DeviceResponse.model_validate(device) for device in devices], request_id=request_id
    )


@router.post(
    "/devices/{device_id}/revoke",
    response_model=Envelope[DeviceResponse],
    summary="Revoke a device and cut its sessions",
)
async def revoke_device(
    device_id: UUID, session: SessionDep, context: AdminDep, request_id: RequestIdDep
) -> Envelope[DeviceResponse]:
    device = await service.revoke_device(
        session, context, device_id=device_id, request_id=request_id
    )
    return Envelope(data=DeviceResponse.model_validate(device), request_id=request_id)

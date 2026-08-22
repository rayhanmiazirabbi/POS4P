from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status

from app.context import RequestContext
from app.dependencies import RequestIdDep, SessionDep, require_roles
from app.models import RecordStatus, Role
from app.schemas.base import Envelope, Page
from app.schemas.users import (
    MembershipResponse,
    MembershipStatus,
    PinSetRequest,
    PinStatusResponse,
    StoreAssignmentRequest,
    StoreMembershipResponse,
    UserCreateRequest,
    UserProfileResponse,
    UserResponse,
    UserRoleUpdateRequest,
    UserStatusUpdateRequest,
    UserUpdateRequest,
)
from app.services import users as service

router = APIRouter(prefix="/users", tags=["Users"])

#: Staff administration maps onto ``users.manage`` in ``@pharmacy/permissions``, which
#: owners and managers hold and cashiers/inventory staff do not. Reads are gated too:
#: the roster carries every colleague's phone number.
UserAdminDep = Annotated[RequestContext, Depends(require_roles(Role.OWNER, Role.MANAGER))]


def _membership_status(active: bool) -> MembershipStatus:
    return "active" if active else "inactive"


def _profile(profile: service.StaffProfile, context: RequestContext) -> UserProfileResponse:
    return UserProfileResponse(
        **UserResponse.model_validate(profile.user).model_dump(),
        membership=MembershipResponse(
            user_id=profile.user.id,
            organization_id=context.organization_id,
            role=profile.membership.role,
            status=_membership_status(profile.membership.active),
        ),
        store_memberships=[
            StoreMembershipResponse(
                user_id=profile.user.id,
                store_id=row.store_id,
                role=row.role,
                status=_membership_status(row.active),
            )
            for row in profile.store_memberships
        ],
        pin_set=profile.user.pin_hash is not None,
    )


def _pin_status(profile: service.StaffProfile) -> PinStatusResponse:
    return PinStatusResponse(user_id=profile.user.id, pin_set=profile.user.pin_hash is not None)


@router.get(
    "",
    response_model=Envelope[Page[UserProfileResponse]],
    summary="List the organization's staff",
)
async def list_users(
    session: SessionDep,
    context: UserAdminDep,
    request_id: RequestIdDep,
    role: Annotated[Role | None, Query()] = None,
    user_status: Annotated[RecordStatus | None, Query(alias="status")] = None,
    store_id: Annotated[UUID | None, Query(alias="storeId")] = None,
) -> Envelope[Page[UserProfileResponse]]:
    profiles = await service.list_staff(
        session, context, role=role, status=user_status, store_id=store_id
    )
    items = [_profile(profile, context) for profile in profiles]
    return Envelope(data=Page(items=items, total=len(items)), request_id=request_id)


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=Envelope[UserProfileResponse],
    summary="Create a staff account and its organization membership",
)
async def create_user(
    payload: UserCreateRequest,
    session: SessionDep,
    context: UserAdminDep,
    request_id: RequestIdDep,
) -> Envelope[UserProfileResponse]:
    profile = await service.create_user(session, context, payload, request_id=request_id)
    return Envelope(data=_profile(profile, context), request_id=request_id)


@router.get("/{user_id}", response_model=Envelope[UserProfileResponse])
async def read_user(
    user_id: UUID, session: SessionDep, context: UserAdminDep, request_id: RequestIdDep
) -> Envelope[UserProfileResponse]:
    profile = await service.load_staff(session, context, user_id)
    return Envelope(data=_profile(profile, context), request_id=request_id)


@router.patch(
    "/{user_id}",
    response_model=Envelope[UserProfileResponse],
    summary="Update a staff member's name or phone number",
)
async def update_user(
    user_id: UUID,
    payload: UserUpdateRequest,
    session: SessionDep,
    context: UserAdminDep,
    request_id: RequestIdDep,
) -> Envelope[UserProfileResponse]:
    profile = await service.update_user(session, context, user_id, payload, request_id=request_id)
    return Envelope(data=_profile(profile, context), request_id=request_id)


@router.patch(
    "/{user_id}/role",
    response_model=Envelope[UserProfileResponse],
    summary="Change an organization role",
)
async def change_role(
    user_id: UUID,
    payload: UserRoleUpdateRequest,
    session: SessionDep,
    context: UserAdminDep,
    request_id: RequestIdDep,
) -> Envelope[UserProfileResponse]:
    profile = await service.change_role(session, context, user_id, payload, request_id=request_id)
    return Envelope(data=_profile(profile, context), request_id=request_id)


@router.patch(
    "/{user_id}/status",
    response_model=Envelope[UserProfileResponse],
    summary="Activate or deactivate a staff account",
)
async def change_status(
    user_id: UUID,
    payload: UserStatusUpdateRequest,
    session: SessionDep,
    context: UserAdminDep,
    request_id: RequestIdDep,
) -> Envelope[UserProfileResponse]:
    profile = await service.change_status(session, context, user_id, payload, request_id=request_id)
    return Envelope(data=_profile(profile, context), request_id=request_id)


@router.delete(
    "/{user_id}/membership",
    response_model=Envelope[UserProfileResponse],
    summary="Withdraw organization access without deleting the user",
)
async def remove_membership(
    user_id: UUID, session: SessionDep, context: UserAdminDep, request_id: RequestIdDep
) -> Envelope[UserProfileResponse]:
    profile = await service.remove_membership(session, context, user_id, request_id=request_id)
    return Envelope(data=_profile(profile, context), request_id=request_id)


@router.put(
    "/{user_id}/pin",
    response_model=Envelope[PinStatusResponse],
    summary="Set or reset a staff PIN",
)
async def set_pin(
    user_id: UUID,
    payload: PinSetRequest,
    session: SessionDep,
    context: UserAdminDep,
    request_id: RequestIdDep,
) -> Envelope[PinStatusResponse]:
    profile = await service.set_pin(session, context, user_id, payload, request_id=request_id)
    return Envelope(data=_pin_status(profile), request_id=request_id)


@router.delete(
    "/{user_id}/pin",
    response_model=Envelope[PinStatusResponse],
    summary="Revoke a staff PIN until a new one is issued",
)
async def clear_pin(
    user_id: UUID, session: SessionDep, context: UserAdminDep, request_id: RequestIdDep
) -> Envelope[PinStatusResponse]:
    profile = await service.clear_pin(session, context, user_id, request_id=request_id)
    return Envelope(data=_pin_status(profile), request_id=request_id)


@router.post(
    "/{user_id}/stores",
    status_code=status.HTTP_201_CREATED,
    response_model=Envelope[UserProfileResponse],
    summary="Assign a staff member to a branch",
)
async def assign_store(
    user_id: UUID,
    payload: StoreAssignmentRequest,
    session: SessionDep,
    context: UserAdminDep,
    request_id: RequestIdDep,
) -> Envelope[UserProfileResponse]:
    profile = await service.assign_store(session, context, user_id, payload, request_id=request_id)
    return Envelope(data=_profile(profile, context), request_id=request_id)


@router.delete(
    "/{user_id}/stores/{store_id}",
    response_model=Envelope[UserProfileResponse],
    summary="Remove a staff member's branch assignment",
)
async def unassign_store(
    user_id: UUID,
    store_id: UUID,
    session: SessionDep,
    context: UserAdminDep,
    request_id: RequestIdDep,
) -> Envelope[UserProfileResponse]:
    profile = await service.unassign_store(
        session, context, user_id, store_id, request_id=request_id
    )
    return Envelope(data=_profile(profile, context), request_id=request_id)

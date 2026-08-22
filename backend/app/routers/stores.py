from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, status

from app.context import RequestContext
from app.dependencies import ContextDep, RequestIdDep, SessionDep, require_roles
from app.models import Role, Store
from app.schemas.base import Envelope, Page
from app.schemas.stores import (
    StoreCreateRequest,
    StoreOperatingStatusResponse,
    StoreProfileResponse,
    StoreResponse,
    StoreSettingsResponse,
    StoreSettingsUpdate,
    StoreStatusUpdateRequest,
    StoreUpdateRequest,
)
from app.services import stores as service

router = APIRouter(prefix="/stores", tags=["Stores"])

#: Branch administration maps onto ``store.manage`` in ``@pharmacy/permissions``,
#: which owners and managers hold and cashiers/inventory staff do not.
StoreManagerDep = Annotated[RequestContext, Depends(require_roles(Role.OWNER, Role.MANAGER))]


def _profile(store: Store) -> StoreProfileResponse:
    return StoreProfileResponse(
        **StoreResponse.model_validate(store).model_dump(),
        settings=service.store_settings_of(store),
    )


def _operating_status(store: Store) -> StoreOperatingStatusResponse:
    return StoreOperatingStatusResponse(
        store_id=store.id,
        status=store.status,
        operational=service.is_operational(store),
        timezone=store.timezone,
        local_time=service.local_now(store),
        business_date=service.business_date(store),
    )


@router.get(
    "",
    response_model=Envelope[Page[StoreResponse]],
    summary="List the branches the caller may switch into",
)
async def list_stores(
    session: SessionDep, context: ContextDep, request_id: RequestIdDep
) -> Envelope[Page[StoreResponse]]:
    stores = await service.list_stores(session, context)
    items = [StoreResponse.model_validate(store) for store in stores]
    return Envelope(data=Page(items=items, total=len(items)), request_id=request_id)


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=Envelope[StoreProfileResponse],
    summary="Create the organization's store",
)
async def create_store(
    payload: StoreCreateRequest,
    session: SessionDep,
    context: StoreManagerDep,
    request_id: RequestIdDep,
) -> Envelope[StoreProfileResponse]:
    store = await service.create_store(session, context, payload, request_id=request_id)
    return Envelope(data=_profile(store), request_id=request_id)


@router.get(
    "/current",
    response_model=Envelope[StoreProfileResponse],
    summary="Read the branch the access token is pinned to",
)
async def read_current_store(
    session: SessionDep, context: ContextDep, request_id: RequestIdDep
) -> Envelope[StoreProfileResponse]:
    store = await service.load_current_store(session, context)
    return Envelope(data=_profile(store), request_id=request_id)


@router.get("/current/operating-status", response_model=Envelope[StoreOperatingStatusResponse])
async def read_current_operating_status(
    session: SessionDep, context: ContextDep, request_id: RequestIdDep
) -> Envelope[StoreOperatingStatusResponse]:
    store = await service.load_current_store(session, context)
    return Envelope(data=_operating_status(store), request_id=request_id)


@router.get("/{store_id}", response_model=Envelope[StoreProfileResponse])
async def read_store(
    store_id: UUID, session: SessionDep, context: ContextDep, request_id: RequestIdDep
) -> Envelope[StoreProfileResponse]:
    store = await service.load_store(session, context, store_id)
    return Envelope(data=_profile(store), request_id=request_id)


@router.patch(
    "/{store_id}",
    response_model=Envelope[StoreProfileResponse],
    summary="Update branch name, timezone, or currency",
)
async def update_store(
    store_id: UUID,
    payload: StoreUpdateRequest,
    session: SessionDep,
    context: StoreManagerDep,
    request_id: RequestIdDep,
) -> Envelope[StoreProfileResponse]:
    store = await service.update_store(session, context, store_id, payload, request_id=request_id)
    return Envelope(data=_profile(store), request_id=request_id)


@router.get("/{store_id}/settings", response_model=Envelope[StoreSettingsResponse])
async def read_store_settings(
    store_id: UUID, session: SessionDep, context: ContextDep, request_id: RequestIdDep
) -> Envelope[StoreSettingsResponse]:
    store = await service.load_store(session, context, store_id)
    return Envelope(
        data=StoreSettingsResponse(store_id=store.id, settings=service.store_settings_of(store)),
        request_id=request_id,
    )


@router.patch("/{store_id}/settings", response_model=Envelope[StoreSettingsResponse])
async def update_store_settings(
    store_id: UUID,
    payload: StoreSettingsUpdate,
    session: SessionDep,
    context: StoreManagerDep,
    request_id: RequestIdDep,
) -> Envelope[StoreSettingsResponse]:
    store, settings = await service.update_store_settings(
        session, context, store_id, payload, request_id=request_id
    )
    return Envelope(
        data=StoreSettingsResponse(store_id=store.id, settings=settings), request_id=request_id
    )


@router.get("/{store_id}/operating-status", response_model=Envelope[StoreOperatingStatusResponse])
async def read_operating_status(
    store_id: UUID, session: SessionDep, context: ContextDep, request_id: RequestIdDep
) -> Envelope[StoreOperatingStatusResponse]:
    store = await service.load_store(session, context, store_id)
    return Envelope(data=_operating_status(store), request_id=request_id)


@router.patch(
    "/{store_id}/operating-status",
    response_model=Envelope[StoreOperatingStatusResponse],
    summary="Open, close, or suspend a branch",
)
async def update_operating_status(
    store_id: UUID,
    payload: StoreStatusUpdateRequest,
    session: SessionDep,
    context: StoreManagerDep,
    request_id: RequestIdDep,
) -> Envelope[StoreOperatingStatusResponse]:
    store = await service.update_store_status(
        session, context, store_id, payload, request_id=request_id
    )
    return Envelope(data=_operating_status(store), request_id=request_id)

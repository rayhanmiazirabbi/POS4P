from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status

from app.context import RequestContext
from app.dependencies import (
    ContextDep,
    RequestIdDep,
    SessionDep,
    require_roles,
)
from app.models import Role

from app.schemas.base import Envelope
from app.schemas.sync import (
    DeviceCreate,
    DeviceRevokeRequest,
    DeviceResponse,
    IngestRequest,
    IngestResponse,
    PullChange,
    PullResponse,
)
from app.services import sync as service

router = APIRouter(prefix="/sync", tags=["Sync"])

StoreManagerDep = Annotated[RequestContext, Depends(require_roles(Role.OWNER, Role.MANAGER))]


@router.post(
    "/devices",
    status_code=status.HTTP_201_CREATED,
    response_model=Envelope[DeviceResponse],
    summary="Register a device (owner/manager only)",
)
async def register_device(
    payload: DeviceCreate,
    session: SessionDep,
    context: StoreManagerDep,
    request_id: RequestIdDep,
) -> Envelope[DeviceResponse]:
    device = await service.register_device(session, context, payload.name, payload.device_key)
    await session.commit()
    return Envelope(data=DeviceResponse.model_validate(device), request_id=request_id)


@router.get(
    "/devices",
    response_model=Envelope[list[DeviceResponse]],
    summary="List registered devices",
)
async def list_devices(
    session: SessionDep,
    context: ContextDep,
    request_id: RequestIdDep,
) -> Envelope[list[DeviceResponse]]:
    devices = await service.list_devices(session, context)
    return Envelope(data=[DeviceResponse.model_validate(d) for d in devices], request_id=request_id)


@router.post(
    "/devices/{device_id}/revoke",
    response_model=Envelope[DeviceResponse],
    summary="Revoke a device (owner/manager only)",
)
async def revoke_device(
    device_id: UUID,
    session: SessionDep,
    context: StoreManagerDep,
    request_id: RequestIdDep,
    payload: DeviceRevokeRequest | None = None,
) -> Envelope[DeviceResponse]:
    device = await service.revoke_device(
        session,
        context,
        device_id,
        request_id=request_id,
        reason=payload.reason if payload else None,
    )
    await session.commit()
    return Envelope(data=DeviceResponse.model_validate(device), request_id=request_id)


@router.post(
    "/events",
    response_model=Envelope[IngestResponse],
    summary="Ingest a batch of mutation envelopes (exactly-once per device)",
)
async def ingest_events(
    payload: IngestRequest,
    session: SessionDep,
    context: ContextDep,
    request_id: RequestIdDep,
) -> Envelope[IngestResponse]:
    device = await service.load_active_device(session, context)
    acks = await service.ingest_events(session, context, device, payload.events)
    return Envelope(data=IngestResponse(acks=acks), request_id=request_id)


@router.get(
    "/events",
    response_model=Envelope[PullResponse],
    summary="Pull applied changes after a server-sequence cursor",
)
async def pull_changes(
    session: SessionDep,
    context: ContextDep,
    request_id: RequestIdDep,
    cursor: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> Envelope[PullResponse]:
    device = await service.load_active_device(session, context)
    rows, next_cursor, has_more = await service.pull_changes(
        session, context, device, cursor, limit=limit
    )
    changes = [
        PullChange(
            server_sequence=row.server_sequence,
            event_type=row.event_type,
            payload=row.payload,
            received_at=row.received_at,
        )
        for row in rows
    ]
    return Envelope(
        data=PullResponse(changes=changes, next_cursor=next_cursor, has_more=has_more),
        request_id=request_id,
    )

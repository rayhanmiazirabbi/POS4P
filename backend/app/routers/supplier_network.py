from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status

from app.context import RequestContext
from app.dependencies import (
    RequestIdDep,
    SessionDep,
    require_roles,
)
from app.domains.supplier_network import AcknowledgementStatus
from app.models import Role
from app.schemas.base import Envelope, Page
from app.schemas.supplier_network import (
    AcknowledgementCreateRequest,
    AcknowledgementDecisionRequest,
    AcknowledgementResponse,
    InviteAcceptRequest,
    InviteCreatedResponse,
    InviteCreateRequest,
    InviteResponse,
)
from app.services import supplier_network as service

router = APIRouter(prefix="/supplier-network", tags=["Supplier Network"])

ManagerDep = Annotated[RequestContext, Depends(require_roles(Role.OWNER, Role.MANAGER))]


def _invite_response(invite) -> InviteResponse:
    return InviteResponse(
        id=invite.id,
        supplier_name=invite.supplier_name,
        contact_phone=invite.contact_phone,
        contact_email=invite.contact_email,
        note=invite.note,
        status=invite.status.value,
        expires_at=invite.expires_at,
        decided_at=invite.decided_at,
        accepted_supplier_id=invite.accepted_supplier_id,
        created_at=invite.created_at,
    )


def _acknowledgement_response(acknowledgement) -> AcknowledgementResponse:
    return AcknowledgementResponse(
        id=acknowledgement.id,
        purchase_id=acknowledgement.purchase_id,
        supplier_id=acknowledgement.supplier_id,
        status=acknowledgement.status.value,
        note=acknowledgement.note,
        response_note=acknowledgement.response_note,
        requested_by_user_id=acknowledgement.requested_by_user_id,
        decided_at=acknowledgement.decided_at,
        created_at=acknowledgement.created_at,
    )


@router.post(
    "/invites",
    status_code=status.HTTP_201_CREATED,
    response_model=Envelope[InviteCreatedResponse],
    summary="Invite an external supplier to join the network (owner/manager only)",
)
async def create_invite(
    payload: InviteCreateRequest,
    session: SessionDep,
    context: ManagerDep,
    request_id: RequestIdDep,
) -> Envelope[InviteCreatedResponse]:
    invite, token = await service.create_invite(session, context, payload, request_id=request_id)
    body = _invite_response(invite).model_dump()
    return Envelope(data=InviteCreatedResponse(**body, invite_token=token), request_id=request_id)


@router.get("/invites", response_model=Envelope[Page[InviteResponse]])
async def list_invites(
    session: SessionDep, context: ManagerDep, request_id: RequestIdDep
) -> Envelope[Page[InviteResponse]]:
    invites = await service.list_invites(session, context)
    return Envelope(
        data=Page(items=[_invite_response(invite) for invite in invites], total=len(invites)),
        request_id=request_id,
    )


@router.post("/invites/{invite_id}/cancel", response_model=Envelope[InviteResponse])
async def cancel_invite(
    invite_id: UUID,
    session: SessionDep,
    context: ManagerDep,
    request_id: RequestIdDep,
) -> Envelope[InviteResponse]:
    invite = await service.cancel_invite(session, context, invite_id, request_id=request_id)
    return Envelope(data=_invite_response(invite), request_id=request_id)


@router.post("/invites/accept", response_model=Envelope[InviteResponse])
async def accept_invite(
    payload: InviteAcceptRequest,
    session: SessionDep,
    request_id: RequestIdDep,
) -> Envelope[InviteResponse]:
    """Public acceptance with the invitation token; no platform account needed."""
    invite, _supplier = await service.accept_invite(session, payload.token)
    return Envelope(data=_invite_response(invite), request_id=request_id)


@router.post(
    "/purchases/{purchase_id}/acknowledgements",
    status_code=status.HTTP_201_CREATED,
    response_model=Envelope[AcknowledgementResponse],
    summary="Send a confirmed purchase to the supplier for acknowledgement",
)
async def request_acknowledgement(
    purchase_id: UUID,
    payload: AcknowledgementCreateRequest,
    session: SessionDep,
    context: ManagerDep,
    request_id: RequestIdDep,
) -> Envelope[AcknowledgementResponse]:
    acknowledgement, _token = await service.request_purchase_acknowledgement(
        session, context, purchase_id, payload, request_id=request_id
    )
    return Envelope(data=_acknowledgement_response(acknowledgement), request_id=request_id)


@router.get("/acknowledgements", response_model=Envelope[Page[AcknowledgementResponse]])
async def list_acknowledgements(
    session: SessionDep,
    context: ManagerDep,
    request_id: RequestIdDep,
    purchase_id: Annotated[UUID | None, Query(alias="purchaseId")] = None,
    ack_status: Annotated[AcknowledgementStatus | None, Query(alias="status")] = None,
) -> Envelope[Page[AcknowledgementResponse]]:
    rows = await service.list_acknowledgements(
        session, context, purchase_id=purchase_id, status=ack_status
    )
    return Envelope(
        data=Page(items=[_acknowledgement_response(row) for row in rows], total=len(rows)),
        request_id=request_id,
    )


@router.post("/acknowledgements/{acknowledgement_id}/cancel", response_model=Envelope[AcknowledgementResponse])
async def cancel_acknowledgement(
    acknowledgement_id: UUID,
    session: SessionDep,
    context: ManagerDep,
    request_id: RequestIdDep,
) -> Envelope[AcknowledgementResponse]:
    acknowledgement = await service.cancel_acknowledgement(
        session, context, acknowledgement_id, request_id=request_id
    )
    return Envelope(data=_acknowledgement_response(acknowledgement), request_id=request_id)


@router.post("/acknowledgements/decide", response_model=Envelope[AcknowledgementResponse])
async def decide_acknowledgement(
    payload: AcknowledgementDecisionRequest,
    session: SessionDep,
    request_id: RequestIdDep,
) -> Envelope[AcknowledgementResponse]:
    """Public supplier decision (acknowledge or decline) via the request token."""
    acknowledgement = await service.decide_purchase_acknowledgement(session, payload)
    return Envelope(data=_acknowledgement_response(acknowledgement), request_id=request_id)

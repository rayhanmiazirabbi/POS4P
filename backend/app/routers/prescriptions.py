from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status

from app.dependencies import (
    ContextDep,
    RequestIdDep,
    SessionDep,
    require_roles,
)
from app.domains.prescriptions import PrescriptionStatus
from app.models import Role
from app.schemas.base import Envelope
from app.schemas.prescriptions import (
    PrescriptionAttachRequest,
    PrescriptionCreateRequest,
    PrescriptionFileRequest,
    PrescriptionFileResponse,
    PrescriptionResponse,
    PrescriptionReviewRequest,
    PrescriptionReviewResponse,
)
from app.services import prescriptions as service

router = APIRouter(prefix="/prescriptions", tags=["Prescriptions"])

StaffRolesDep = Annotated[
    object, Depends(require_roles(Role.OWNER, Role.MANAGER, Role.CASHIER))
]
PharmacistDep = Annotated[object, Depends(require_roles(Role.OWNER, Role.MANAGER))]


def _build_response(prescription, files) -> PrescriptionResponse:
    response = PrescriptionResponse.model_validate(prescription)
    response.files = [PrescriptionFileResponse.model_validate(file) for file in files]
    return response


@router.get("", response_model=Envelope[list[PrescriptionResponse]])
async def list_prescriptions(
    session: SessionDep,
    context: ContextDep,
    request_id: RequestIdDep,
    prescription_status: Annotated[PrescriptionStatus | None, Query(alias="status")] = None,
    customer_id: Annotated[UUID | None, Query(alias="customerId")] = None,
    order_id: Annotated[UUID | None, Query(alias="orderId")] = None,
) -> Envelope[list[PrescriptionResponse]]:
    rows = await service.list_prescriptions(
        session,
        context,
        status=prescription_status,
        customer_id=customer_id,
        order_id=order_id,
    )
    grouped = await service.load_files(session, [row.id for row in rows])
    return Envelope(
        data=[_build_response(row, grouped.get(row.id, [])) for row in rows],
        request_id=request_id,
    )


@router.post(
    "",
    response_model=Envelope[PrescriptionResponse],
    status_code=status.HTTP_201_CREATED,
)
async def create_prescription(
    payload: PrescriptionCreateRequest,
    session: SessionDep,
    context: StaffRolesDep,
    request_id: RequestIdDep,
) -> Envelope[PrescriptionResponse]:
    prescription = await service.create_prescription(
        session, context, payload, request_id=request_id
    )
    return Envelope(data=_build_response(prescription, []), request_id=request_id)


@router.post(
    "/{prescription_id}/files",
    response_model=Envelope[PrescriptionResponse],
    summary="Record metadata for an uploaded prescription file (object key only)",
)
async def add_prescription_file(
    prescription_id: UUID,
    payload: PrescriptionFileRequest,
    session: SessionDep,
    context: StaffRolesDep,
    request_id: RequestIdDep,
) -> Envelope[PrescriptionResponse]:
    await service.add_file(session, context, prescription_id, payload, request_id=request_id)
    prescription = await service.load_prescription(session, context, prescription_id)
    files = (await service.load_files(session, [prescription.id])).get(prescription.id, [])
    return Envelope(data=_build_response(prescription, files), request_id=request_id)


@router.post(
    "/{prescription_id}/order",
    response_model=Envelope[PrescriptionResponse],
    summary="Link a prescription to the order it authorizes",
)
async def attach_prescription_to_order(
    prescription_id: UUID,
    payload: PrescriptionAttachRequest,
    session: SessionDep,
    context: StaffRolesDep,
    request_id: RequestIdDep,
) -> Envelope[PrescriptionResponse]:
    prescription = await service.load_prescription(session, context, prescription_id)
    prescription = await service.attach_to_order(
        session, context, prescription, payload.order_id, request_id=request_id
    )
    files = (await service.load_files(session, [prescription.id])).get(prescription.id, [])
    return Envelope(data=_build_response(prescription, files), request_id=request_id)


@router.post("/{prescription_id}/review", response_model=Envelope[PrescriptionReviewResponse])
async def review_prescription(
    prescription_id: UUID,
    payload: PrescriptionReviewRequest,
    session: SessionDep,
    context: PharmacistDep,
    request_id: RequestIdDep,
) -> Envelope[PrescriptionReviewResponse]:
    prescription, review = await service.review_prescription(
        session, context, prescription_id, payload, request_id=request_id
    )
    return Envelope(data=PrescriptionReviewResponse.model_validate(review), request_id=request_id)

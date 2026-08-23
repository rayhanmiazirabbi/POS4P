from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from app.dependencies import (
    ContextDep,
    RequestIdDep,
    SessionDep,
    require_roles,
)
from app.domains.payments import PaymentMethod, PaymentStatus
from app.models import Role
from app.schemas.base import Envelope, Page
from app.schemas.payments import PaymentResponse, PaymentStatusUpdateRequest
from app.services import payments as service

router = APIRouter(prefix="/payments", tags=["Payments"])

StoreManagerDep = Annotated[object, Depends(require_roles(Role.OWNER, Role.MANAGER))]


@router.get(
    "",
    response_model=Envelope[Page[PaymentResponse]],
    summary="List payments with optional reference filters",
)
async def list_payments(
    session: SessionDep,
    context: ContextDep,
    request_id: RequestIdDep,
    reference_type: Annotated[str | None, Query(alias="referenceType")] = None,
    reference_id: Annotated[UUID | None, Query(alias="referenceId")] = None,
    customer_id: Annotated[UUID | None, Query(alias="customerId")] = None,
    method: PaymentMethod | None = None,
    payment_status: Annotated[PaymentStatus | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Envelope[Page[PaymentResponse]]:
    rows, total = await service.list_payments(
        session,
        context,
        reference_type=reference_type,
        reference_id=reference_id,
        customer_id=customer_id,
        method=method,
        status=payment_status,
        limit=limit,
        offset=offset,
    )
    items = [PaymentResponse.model_validate(payment) for payment in rows]
    return Envelope(data=Page(items=items, total=total), request_id=request_id)


@router.get("/{payment_id}", response_model=Envelope[PaymentResponse])
async def read_payment(
    payment_id: UUID,
    session: SessionDep,
    context: ContextDep,
    request_id: RequestIdDep,
) -> Envelope[PaymentResponse]:
    payment = await service.load_payment(session, context, payment_id)
    return Envelope(data=PaymentResponse.model_validate(payment), request_id=request_id)


@router.post(
    "/{payment_id}/status",
    response_model=Envelope[PaymentResponse],
    summary="Manually resolve a pending payment (owner/manager only)",
)
async def update_payment_status(
    payment_id: UUID,
    payload: PaymentStatusUpdateRequest,
    session: SessionDep,
    context: StoreManagerDep,
    request_id: RequestIdDep,
) -> Envelope[PaymentResponse]:
    payment = await service.update_payment_status(
        session,
        context,
        payment_id,
        payload.status,
        provider_reference=payload.provider_reference,
        request_id=request_id,
    )
    return Envelope(data=PaymentResponse.model_validate(payment), request_id=request_id)

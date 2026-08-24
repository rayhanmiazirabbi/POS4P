from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status

from app.context import RequestContext
from app.dependencies import (
    ContextDep,
    IdempotencyKeyDep,
    RequestIdDep,
    SessionDep,
    require_roles,
)
from app.errors import ValidationError
from app.models import Role
from app.schemas.ai import (
    AIConfirmationRequest,
    AIConfirmationResponse,
    AIJobCreateRequest,
    AIJobResponse,
    PurchaseDraftRequest,
)
from app.schemas.base import Envelope, Page
from app.schemas.purchasing import PurchaseResponse
from app.services import ai as service

router = APIRouter(prefix="/ai", tags=["AI"])

ManagerDep = Annotated[RequestContext, Depends(require_roles(Role.OWNER, Role.MANAGER))]


def _job_response(job) -> AIJobResponse:
    return AIJobResponse(
        id=job.id,
        job_type=job.job_type,
        status=job.status.value,
        provider=job.provider,
        model_version=job.model_version,
        confidence=job.confidence,
        result=job.result,
        error=job.error,
        created_at=job.created_at,
        completed_at=job.completed_at,
    )


@router.post("/jobs", status_code=status.HTTP_201_CREATED, response_model=Envelope[AIJobResponse])
async def create_job(
    payload: AIJobCreateRequest,
    session: SessionDep,
    context: ContextDep,
    request_id: RequestIdDep,
    idempotency_key: IdempotencyKeyDep,
) -> Envelope[AIJobResponse]:
    """Create and run one assistant job; the same key returns the original job."""
    if not idempotency_key:
        raise ValidationError("Idempotency-Key header required")
    job = await service.create_job(
        session,
        context,
        payload,
        idempotency_key=idempotency_key,
        request_id=request_id,
    )
    return Envelope(data=_job_response(job), request_id=request_id)


@router.get("/jobs", response_model=Envelope[Page[AIJobResponse]])
async def list_jobs(
    session: SessionDep,
    context: ContextDep,
    request_id: RequestIdDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Envelope[Page[AIJobResponse]]:
    rows, total = await service.list_jobs(session, context, limit=limit, offset=offset)
    return Envelope(
        data=Page(items=[_job_response(job) for job in rows], total=total), request_id=request_id
    )


@router.get("/jobs/{job_id}", response_model=Envelope[AIJobResponse])
async def read_job(
    job_id: UUID,
    session: SessionDep,
    context: ContextDep,
    request_id: RequestIdDep,
) -> Envelope[AIJobResponse]:
    job = await service.load_job(session, context, job_id)
    return Envelope(data=_job_response(job), request_id=request_id)


@router.post(
    "/jobs/{job_id}/confirmations",
    status_code=status.HTTP_201_CREATED,
    response_model=Envelope[AIConfirmationResponse],
)
async def confirm_job(
    job_id: UUID,
    payload: AIConfirmationRequest,
    session: SessionDep,
    context: ManagerDep,
    request_id: RequestIdDep,
) -> Envelope[AIConfirmationResponse]:
    """The human gate: an owner/manager accepts or rejects an AI result."""
    confirmation = await service.confirm_job(session, context, job_id, payload, request_id=request_id)
    return Envelope(
        data=AIConfirmationResponse.model_validate(confirmation), request_id=request_id
    )


@router.post(
    "/jobs/{job_id}/purchase-draft",
    status_code=status.HTTP_201_CREATED,
    response_model=Envelope[PurchaseResponse],
)
async def create_purchase_draft(
    job_id: UUID,
    payload: PurchaseDraftRequest,
    session: SessionDep,
    context: ManagerDep,
    request_id: RequestIdDep,
) -> Envelope[PurchaseResponse]:
    """Convert an accepted extraction into a DRAFT purchase (never auto-confirmed)."""
    purchase = await service.create_purchase_draft_from_job(
        session, context, job_id, payload, request_id=request_id
    )
    return Envelope(data=PurchaseResponse.model_validate(purchase), request_id=request_id)

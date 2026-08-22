from __future__ import annotations

from decimal import Decimal
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, status

from app.context import RequestContext
from app.dependencies import (
    ContextDep,
    RequestIdDep,
    SessionDep,
    require_roles,
)
from app.domains.purchasing import PurchaseStatus
from app.errors import ValidationError
from app.models import Role
from app.schemas.base import Envelope, Page
from app.schemas.purchasing import (
    PurchaseCreateRequest,
    PurchaseItemResponse,
    PurchaseResponse,
    PurchaseReturnRequest,
    PurchaseReturnLine,
)
from app.services import purchasing as service

router = APIRouter(prefix="/purchases", tags=["Purchasing"])

StoreManagerDep = Annotated[RequestContext, Depends(require_roles(Role.OWNER, Role.MANAGER))]


async def require_idempotency_key(
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> str:
    if not idempotency_key or not 16 <= len(idempotency_key.strip()) <= 128:
        raise ValidationError("Idempotency-Key header is required", code="VALIDATION_ERROR")
    return idempotency_key.strip()


IdempotentDep = Annotated[str, Depends(require_idempotency_key)]


def _item(item, *, include_costs: bool) -> PurchaseItemResponse:
    data = PurchaseItemResponse.model_validate(item)
    if not include_costs:
        data.unit_cost = None
    return data


def _purchase(
    purchase,
    items,
    *,
    include_costs: bool,
) -> PurchaseResponse:
    data = PurchaseResponse(
        id=purchase.id,
        organization_id=purchase.organization_id,
        store_id=purchase.store_id,
        supplier_id=purchase.supplier_id,
        status=purchase.status,
        invoice_number=purchase.invoice_number,
        note=purchase.note,
        purchased_at=purchase.purchased_at,
        confirmed_at=purchase.confirmed_at,
        total_amount=Decimal(purchase.total_amount) if include_costs else None,
        items=[_item(item, include_costs=include_costs) for item in items],
    )
    return data


@router.get(
    "",
    response_model=Envelope[Page[PurchaseResponse]],
    summary="List purchases with optional status/supplier filters",
)
async def list_purchases(
    session: SessionDep,
    context: ContextDep,
    request_id: RequestIdDep,
    purchase_status: Annotated[PurchaseStatus | None, Query(alias="status")] = None,
    supplier_id: UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Envelope[Page[PurchaseResponse]]:
    rows, total = await service.list_purchases(
        session,
        context,
        status=purchase_status,
        supplier_id=supplier_id,
        limit=limit,
        offset=offset,
    )
    include_costs = service.can_see_costs(context)
    items = [_purchase(purchase, [], include_costs=include_costs) for purchase in rows]
    return Envelope(data=Page(items=items, total=total), request_id=request_id)


@router.get("/{purchase_id}", response_model=Envelope[PurchaseResponse])
async def read_purchase(
    purchase_id: UUID,
    session: SessionDep,
    context: ContextDep,
    request_id: RequestIdDep,
) -> Envelope[PurchaseResponse]:
    purchase, items = await service.get_purchase_with_items(session, context, purchase_id)
    include_costs = service.can_see_costs(context)
    return Envelope(
        data=_purchase(purchase, items, include_costs=include_costs), request_id=request_id
    )


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=Envelope[PurchaseResponse],
    summary="Create a draft purchase (owner/manager only)",
)
async def create_purchase(
    payload: PurchaseCreateRequest,
    session: SessionDep,
    context: StoreManagerDep,
    request_id: RequestIdDep,
) -> Envelope[PurchaseResponse]:
    purchase = await service.create_purchase(session, context, payload, request_id=request_id)
    items = await service.get_purchase_with_items(session, context, purchase.id)
    return Envelope(data=_purchase(purchase, items[1], include_costs=True), request_id=request_id)


@router.post(
    "/{purchase_id}/confirm",
    response_model=Envelope[PurchaseResponse],
    summary="Confirm a draft: receive batches, book supplier due (idempotent)",
)
async def confirm_purchase(
    purchase_id: UUID,
    session: SessionDep,
    context: StoreManagerDep,
    request_id: RequestIdDep,
    idempotency_key: IdempotentDep,
) -> Envelope[PurchaseResponse]:
    purchase = await service.confirm_purchase(
        session, context, purchase_id, idempotency_key=idempotency_key, request_id=request_id
    )
    _, items = await service.get_purchase_with_items(session, context, purchase.id)
    return Envelope(data=_purchase(purchase, items, include_costs=True), request_id=request_id)


@router.post(
    "/{purchase_id}/returns",
    status_code=status.HTTP_201_CREATED,
    response_model=Envelope[PurchaseResponse],
    summary="Return lines of a confirmed purchase to the supplier",
)
async def create_return(
    purchase_id: UUID,
    payload: PurchaseReturnRequest,
    session: SessionDep,
    context: StoreManagerDep,
    request_id: RequestIdDep,
) -> Envelope[PurchaseResponse]:
    returned = await service.return_purchase(session, context, purchase_id, payload, request_id=request_id)
    items = await service.get_purchase_with_items(session, context, returned.id)
    return Envelope(data=_purchase(returned, items[1], include_costs=True), request_id=request_id)




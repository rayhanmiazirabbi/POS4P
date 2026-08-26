from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, status

from app.context import RequestContext
from app.dependencies import (
    ContextDep,
    RequestIdDep,
    SessionDep,
    StoreContextDep,
    require_roles,
)
from app.domains.purchase_orders import PurchaseOrderStatus
from app.errors import ValidationError
from app.models import Role
from app.schemas.base import Envelope, Page
from app.schemas.purchase_orders import (
    PurchaseOrderConvertRequest,
    PurchaseOrderConvertResult,
    PurchaseOrderCreateRequest,
    PurchaseOrderItemCreate,
    PurchaseOrderItemResponse,
    PurchaseOrderItemUpdate,
    PurchaseOrderResponse,
)
from app.services import purchase_orders as service

router = APIRouter(prefix="/purchase-orders", tags=["Purchase Orders"])

#: Ordering paperwork belongs to every store role; converting into a purchase
#: (which books supplier costs) stays owner/manager, mirroring ``POST /purchases``.
PoConverterDep = Annotated[RequestContext, Depends(require_roles(Role.OWNER, Role.MANAGER))]


async def require_idempotency_key(
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> str:
    if not idempotency_key or not 16 <= len(idempotency_key.strip()) <= 128:
        raise ValidationError("Idempotency-Key header is required", code="VALIDATION_ERROR")
    return idempotency_key.strip()


IdempotentDep = Annotated[str, Depends(require_idempotency_key)]


@router.get(
    "",
    response_model=Envelope[Page[PurchaseOrderResponse]],
    summary="List purchase orders with an optional status filter",
)
async def list_purchase_orders(
    session: SessionDep,
    context: ContextDep,
    request_id: RequestIdDep,
    po_status: Annotated[PurchaseOrderStatus | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Envelope[Page[PurchaseOrderResponse]]:
    rows, total = await service.list_orders(session, context, status=po_status, limit=limit, offset=offset)
    items = [service.order_response(row, []) for row in rows]
    return Envelope(data=Page(items=items, total=total), request_id=request_id)


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=Envelope[PurchaseOrderResponse],
    summary="Create a draft purchase order (all store roles)",
)
async def create_purchase_order(
    payload: PurchaseOrderCreateRequest,
    session: SessionDep,
    context: StoreContextDep,
    request_id: RequestIdDep,
    idempotency_key: IdempotentDep,
) -> Envelope[PurchaseOrderResponse]:
    row = await service.create_order(
        session, context, payload, idempotency_key=idempotency_key, request_id=request_id
    )
    return Envelope(data=await service.get_with_items(session, context, row.id), request_id=request_id)


@router.get("/{po_id}", response_model=Envelope[PurchaseOrderResponse])
async def read_purchase_order(
    po_id: UUID, session: SessionDep, context: ContextDep, request_id: RequestIdDep
) -> Envelope[PurchaseOrderResponse]:
    return Envelope(data=await service.get_with_items(session, context, po_id), request_id=request_id)


@router.post(
    "/{po_id}/items",
    status_code=status.HTTP_201_CREATED,
    response_model=Envelope[PurchaseOrderItemResponse],
    summary="Append a line to a draft purchase order",
)
async def add_item(
    po_id: UUID,
    payload: PurchaseOrderItemCreate,
    session: SessionDep,
    context: StoreContextDep,
    request_id: RequestIdDep,
) -> Envelope[PurchaseOrderItemResponse]:
    item = await service.add_item(session, context, po_id, payload, request_id=request_id)
    return Envelope(data=PurchaseOrderItemResponse.model_validate(item), request_id=request_id)


@router.patch("/{po_id}/items/{item_id}", response_model=Envelope[PurchaseOrderItemResponse])
async def update_item(
    po_id: UUID,
    item_id: UUID,
    payload: PurchaseOrderItemUpdate,
    session: SessionDep,
    context: StoreContextDep,
    request_id: RequestIdDep,
) -> Envelope[PurchaseOrderItemResponse]:
    item = await service.update_item(
        session, context, po_id, item_id, payload, request_id=request_id
    )
    return Envelope(data=PurchaseOrderItemResponse.model_validate(item), request_id=request_id)


@router.delete("/{po_id}/items/{item_id}", response_model=Envelope[PurchaseOrderItemResponse])
async def remove_item(
    po_id: UUID,
    item_id: UUID,
    session: SessionDep,
    context: StoreContextDep,
    request_id: RequestIdDep,
) -> Envelope[PurchaseOrderItemResponse]:
    item = await service.remove_item(session, context, po_id, item_id, request_id=request_id)
    return Envelope(data=PurchaseOrderItemResponse.model_validate(item), request_id=request_id)


@router.post(
    "/{po_id}/order",
    response_model=Envelope[PurchaseOrderResponse],
    summary="Mark a draft as ordered",
)
async def mark_ordered(
    po_id: UUID, session: SessionDep, context: StoreContextDep, request_id: RequestIdDep
) -> Envelope[PurchaseOrderResponse]:
    row = await service.mark_ordered(session, context, po_id, request_id=request_id)
    return Envelope(data=await service.get_with_items(session, context, row.id), request_id=request_id)


@router.post(
    "/{po_id}/close",
    response_model=Envelope[PurchaseOrderResponse],
    summary="Close an ordered purchase order",
)
async def close_order(
    po_id: UUID, session: SessionDep, context: StoreContextDep, request_id: RequestIdDep
) -> Envelope[PurchaseOrderResponse]:
    row = await service.close_order(session, context, po_id, request_id=request_id)
    return Envelope(data=await service.get_with_items(session, context, row.id), request_id=request_id)


@router.post(
    "/{po_id}/cancel",
    response_model=Envelope[PurchaseOrderResponse],
    summary="Cancel an open purchase order",
)
async def cancel_order(
    po_id: UUID, session: SessionDep, context: StoreContextDep, request_id: RequestIdDep
) -> Envelope[PurchaseOrderResponse]:
    row = await service.cancel_order(session, context, po_id, request_id=request_id)
    return Envelope(data=await service.get_with_items(session, context, row.id), request_id=request_id)


@router.post(
    "/{po_id}/to-purchase",
    response_model=Envelope[PurchaseOrderConvertResult],
    summary="Convert resolvable lines into a purchase draft (owner/manager)",
)
async def convert_to_purchase(
    po_id: UUID,
    payload: PurchaseOrderConvertRequest,
    session: SessionDep,
    context: PoConverterDep,
    request_id: RequestIdDep,
) -> Envelope[PurchaseOrderConvertResult]:
    result = await service.convert_to_purchase(
        session, context, po_id, payload, request_id=request_id
    )
    return Envelope(data=result, request_id=request_id)

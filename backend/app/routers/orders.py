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
from app.domains.orders import OrderStatus
from app.models import Role
from app.routers.purchasing import IdempotentDep
from app.schemas.base import Envelope
from app.schemas.orders import (
    OrderCreateRequest,
    OrderResponse,
    OrderTransitionRequest,
)
from app.services import orders as service

router = APIRouter(prefix="/orders", tags=["Orders"])

StaffRolesDep = Annotated[
    object, Depends(require_roles(Role.OWNER, Role.MANAGER, Role.CASHIER))
]


def _build_response(order, items, history) -> OrderResponse:
    from app.schemas.orders import OrderItemResponse, OrderStatusHistoryResponse

    response = OrderResponse.model_validate(order)
    response.items = [OrderItemResponse.model_validate(item) for item in items]
    response.history = [
        OrderStatusHistoryResponse.model_validate(entry) for entry in history
    ]
    return response


@router.get("", response_model=Envelope[list[OrderResponse]])
async def list_orders(
    session: SessionDep,
    context: ContextDep,
    request_id: RequestIdDep,
    order_status: Annotated[OrderStatus | None, Query(alias="status")] = None,
    customer_id: Annotated[UUID | None, Query(alias="customerId")] = None,
) -> Envelope[list[OrderResponse]]:
    rows = await service.list_orders(session, context, status=order_status, customer_id=customer_id)
    maps = await service.load_order_maps(session, rows)
    items = [
        _build_response(order, *maps[order.id]) for order in rows
    ]
    return Envelope(data=items, request_id=request_id)


@router.post(
    "",
    response_model=Envelope[OrderResponse],
    status_code=status.HTTP_201_CREATED,
    summary="Guest checkout; requires an Idempotency-Key",
)
async def create_order(
    payload: OrderCreateRequest,
    session: SessionDep,
    context: StaffRolesDep,
    request_id: RequestIdDep,
    idempotency_key: IdempotentDep,
) -> Envelope[OrderResponse]:
    order, items, history = await service.create_order(
        session,
        context,
        payload,
        idempotency_key=idempotency_key,
        request_id=request_id,
    )
    return Envelope(data=_build_response(order, items, history), request_id=request_id)


@router.get("/{order_id}", response_model=Envelope[OrderResponse])
async def read_order(
    order_id: UUID,
    session: SessionDep,
    context: ContextDep,
    request_id: RequestIdDep,
) -> Envelope[OrderResponse]:
    order, items, history = await service.load_order_detail(session, context, order_id)
    return Envelope(data=_build_response(order, items, history), request_id=request_id)


@router.post("/{order_id}/transition", response_model=Envelope[OrderResponse])
async def transition_order(
    order_id: UUID,
    payload: OrderTransitionRequest,
    session: SessionDep,
    context: StaffRolesDep,
    request_id: RequestIdDep,
) -> Envelope[OrderResponse]:
    order, items, history = await service.transition_order(
        session,
        context,
        order_id,
        payload.status,
        request_id=request_id,
    )
    return Envelope(data=_build_response(order, items, history), request_id=request_id)

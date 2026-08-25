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
from app.domains.sales import SaleStatus
from app.models import Role
from app.schemas.base import Envelope, Page
from app.schemas.sales import (
    SaleCreateRequest,
    SaleResponse,
    SaleReturnRequest,
    SaleReturnResponse,
    SaleVoidRequest,
)
from app.services import sales as service
from app.routers.purchasing import require_idempotency_key

router = APIRouter(prefix="/sales", tags=["Sales"])

StaffRolesDep = Annotated[
    object, Depends(require_roles(Role.OWNER, Role.MANAGER, Role.CASHIER))
]
StoreManagerDep = Annotated[object, Depends(require_roles(Role.OWNER, Role.MANAGER))]
IdempotentDep = Annotated[str, Depends(require_idempotency_key)]


def _response(sale, items, payments) -> SaleResponse:
    return SaleResponse.model_validate(service.sale_body(sale, items, payments))


@router.get(
    "",
    response_model=Envelope[Page[SaleResponse]],
    summary="List sales with optional customer/status filters",
)
async def list_sales(
    session: SessionDep,
    context: ContextDep,
    request_id: RequestIdDep,
    customer_id: Annotated[UUID | None, Query(alias="customerId")] = None,
    sale_status: Annotated[SaleStatus | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Envelope[Page[SaleResponse]]:
    rows, total = await service.list_sales(
        session,
        context,
        customer_id=customer_id,
        status=sale_status,
        limit=limit,
        offset=offset,
    )
    items = []
    for sale in rows:
        _, sale_items, payments = await service.get_sale_detail(session, context, sale.id)
        items.append(_response(sale, sale_items, payments))
    return Envelope(data=Page(items=items, total=total), request_id=request_id)


@router.get("/{sale_id}", response_model=Envelope[SaleResponse])
async def read_sale(
    sale_id: UUID,
    session: SessionDep,
    context: ContextDep,
    request_id: RequestIdDep,
) -> Envelope[SaleResponse]:
    sale, items, payments = await service.get_sale_detail(session, context, sale_id)
    return Envelope(data=_response(sale, items, payments), request_id=request_id)


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=Envelope[SaleResponse],
    summary="Create a completed POS sale (idempotent)",
)
async def create_sale(
    payload: SaleCreateRequest,
    session: SessionDep,
    context: StaffRolesDep,
    request_id: RequestIdDep,
    idempotency_key: IdempotentDep,
) -> Envelope[SaleResponse]:
    result = await service.create_sale(
        session, context, payload, idempotency_key=idempotency_key, request_id=request_id
    )
    if result.replay_body is not None:
        return Envelope(
            data=SaleResponse.model_validate(result.replay_body), request_id=request_id
        )
    return Envelope(data=_response(result.sale, result.items, result.payments), request_id=request_id)


@router.post(
    "/{sale_id}/returns",
    status_code=status.HTTP_201_CREATED,
    response_model=Envelope[SaleReturnResponse],
    summary="Return lines of a completed sale back into stock",
)
async def create_return(
    sale_id: UUID,
    payload: SaleReturnRequest,
    session: SessionDep,
    context: StaffRolesDep,
    request_id: RequestIdDep,
    idempotency_key: IdempotentDep,
) -> Envelope[SaleReturnResponse]:
    result = await service.create_sale_return(
        session, context, sale_id, payload, idempotency_key=idempotency_key, request_id=request_id
    )
    if result.replay_body is not None:
        return Envelope(
            data=SaleReturnResponse.model_validate(result.replay_body), request_id=request_id
        )
    return Envelope(
        data=SaleReturnResponse.model_validate(result.sale_return), request_id=request_id
    )


@router.post(
    "/{sale_id}/void",
    response_model=Envelope[SaleResponse],
    summary="Void a same-day sale (owner/manager only)",
)
async def void_sale(
    sale_id: UUID,
    payload: SaleVoidRequest,
    session: SessionDep,
    context: StoreManagerDep,
    request_id: RequestIdDep,
) -> Envelope[SaleResponse]:
    sale = await service.void_sale(session, context, sale_id, payload, request_id=request_id)
    _, items, payments = await service.get_sale_detail(session, context, sale.id)
    return Envelope(data=_response(sale, items, payments), request_id=request_id)

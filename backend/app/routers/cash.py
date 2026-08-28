from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status

from app.context import RequestContext
from app.dependencies import (
    RequestIdDep,
    SessionDep,
    StoreContextDep,
    require_roles,
)
from app.models import Role
from app.schemas.base import Envelope, Page
from app.schemas.cash import (
    CashSessionCloseRequest,
    CashSessionOpenRequest,
    CashSessionResponse,
)
from app.services import cash as service

router = APIRouter(prefix="/cash-sessions", tags=["Cash sessions"])

StaffRolesDep = Annotated[
    RequestContext, Depends(require_roles(Role.OWNER, Role.MANAGER, Role.CASHIER))
]


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=Envelope[CashSessionResponse],
    summary="Open the branch's cash session with a starting float",
)
async def open_session(
    payload: CashSessionOpenRequest,
    session: SessionDep,
    context: StaffRolesDep,
    request_id: RequestIdDep,
) -> Envelope[CashSessionResponse]:
    cash = await service.open_session(
        session, context, payload.opening_cash, request_id=request_id
    )
    return Envelope(
        data=await service.session_response(session, context, cash), request_id=request_id
    )


@router.get(
    "/current",
    response_model=Envelope[CashSessionResponse | None],
    summary="The branch's open cash session, if any",
)
async def read_current_session(
    session: SessionDep,
    context: StoreContextDep,
    request_id: RequestIdDep,
) -> Envelope[CashSessionResponse | None]:
    cash = await service.current_session(session, context)
    data = None if cash is None else await service.session_response(session, context, cash)
    return Envelope(data=data, request_id=request_id)


@router.post(
    "/{session_id}/close",
    response_model=Envelope[CashSessionResponse],
    summary="Close the cash session with a counted drawer",
)
async def close_session(
    session_id: UUID,
    payload: CashSessionCloseRequest,
    session: SessionDep,
    context: StaffRolesDep,
    request_id: RequestIdDep,
) -> Envelope[CashSessionResponse]:
    cash = await service.close_session(
        session,
        context,
        session_id,
        payload.counted_cash,
        payload.note,
        request_id=request_id,
    )
    return Envelope(
        data=await service.session_response(session, context, cash), request_id=request_id
    )


@router.get(
    "",
    response_model=Envelope[Page[CashSessionResponse]],
    summary="List this branch's cash sessions, newest first",
)
async def list_sessions(
    session: SessionDep,
    context: StoreContextDep,
    request_id: RequestIdDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Envelope[Page[CashSessionResponse]]:
    rows, total = await service.list_sessions(session, context, limit=limit, offset=offset)
    items = [await service.session_response(session, context, row) for row in rows]
    return Envelope(data=Page(items=items, total=total), request_id=request_id)

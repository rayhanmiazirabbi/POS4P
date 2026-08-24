from __future__ import annotations

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status

from app.context import RequestContext
from app.dependencies import ContextDep, RequestIdDep, SessionDep, require_roles
from app.errors import ValidationError
from app.models import Role
from app.schemas.base import Envelope
from app.schemas.loyalty import (
    LoyaltyAccountResponse,
    LoyaltyEnrollRequest,
    LoyaltyRebuildResponse,
    LoyaltyTransactionRequest,
    LoyaltyTransactionResponse,
)
from app.services import loyalty as service

router = APIRouter(prefix="/loyalty", tags=["Loyalty"])

OwnerManagerDep = Annotated[RequestContext, Depends(require_roles(Role.OWNER, Role.MANAGER))]


async def require_idempotency_key(
    idempotency_key: Annotated[str | None, Query(alias="idempotencyKey")] = None,
) -> str:
    if not idempotency_key or not 16 <= len(idempotency_key.strip()) <= 128:
        raise ValidationError("idempotencyKey query parameter is required")
    return idempotency_key.strip()


IdempotentDep = Annotated[str, Depends(require_idempotency_key)]


@router.post(
    "/accounts",
    status_code=status.HTTP_201_CREATED,
    response_model=Envelope[LoyaltyAccountResponse],
)
async def enroll_customer(
    payload: LoyaltyEnrollRequest,
    session: SessionDep,
    context: ContextDep,
    request_id: RequestIdDep,
) -> Envelope[LoyaltyAccountResponse]:
    """Idempotent enrollment: an existing account is returned unchanged."""
    account = await service.enroll_customer(
        session, context, payload.customer_id, request_id=request_id
    )
    return Envelope(data=service.balance_response(account).account, request_id=request_id)


@router.get("/accounts/{account_id}", response_model=Envelope[LoyaltyAccountResponse])
async def read_account(
    account_id: UUID,
    session: SessionDep,
    context: ContextDep,
    request_id: RequestIdDep,
) -> Envelope[LoyaltyAccountResponse]:
    account = await service.load_account(session, context, account_id)
    return Envelope(data=service.balance_response(account).account, request_id=request_id)


@router.post(
    "/accounts/{account_id}/transactions",
    response_model=Envelope[LoyaltyTransactionResponse],
)
async def post_transaction(
    account_id: UUID,
    payload: LoyaltyTransactionRequest,
    session: SessionDep,
    context: ContextDep,
    request_id: RequestIdDep,
    idempotency_key: IdempotentDep,
) -> Envelope[LoyaltyTransactionResponse]:
    """Post earn/redeem/refund/bonus against the ledger; idempotent per key."""
    if payload.transaction_type == "adjust":
        transaction, account = await service.apply_adjustment(
            session,
            context,
            account_id,
            payload,
            idempotency_key=idempotency_key,
            request_id=request_id,
        )
    else:
        transaction, account = await service.apply_transaction(
            session,
            context,
            account_id,
            payload,
            idempotency_key=idempotency_key,
            request_id=request_id,
        )
    return Envelope(
        data=service.transaction_view(transaction, int(account.balance)),
        request_id=request_id,
    )


@router.get("/accounts/{account_id}/transactions", response_model=Envelope[list[LoyaltyTransactionResponse]])
async def list_transactions(
    account_id: UUID,
    session: SessionDep,
    context: ContextDep,
    request_id: RequestIdDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Envelope[list[LoyaltyTransactionResponse]]:
    """Ledger rows newest first."""
    from sqlalchemy import select

    from app.domains.loyalty import LoyaltyTransaction

    await service.load_account(session, context, account_id)
    rows = list(
        await session.scalars(
            select(LoyaltyTransaction)
            .where(LoyaltyTransaction.account_id == account_id)
            .order_by(LoyaltyTransaction.created_at.desc(), LoyaltyTransaction.id)
            .limit(limit)
            .offset(offset)
        )
    )
    return Envelope(
        data=[service.transaction_view(tx) for tx in rows],
        request_id=request_id,
    )


@router.post("/accounts/{account_id}/expire", response_model=Envelope[list[LoyaltyTransactionResponse]])
async def expire_points(
    account_id: UUID,
    session: SessionDep,
    context: OwnerManagerDep,
    request_id: RequestIdDep,
    as_of: Annotated[datetime | None, Query(alias="asOf")] = None,
) -> Envelope[list[LoyaltyTransactionResponse]]:
    """Expire due lots and post EXPIRE ledger rows (owner/manager only)."""
    transactions = await service.expire_due_points(
        session, context, account_id, as_of=as_of, request_id=request_id
    )
    account = await service.load_account(session, context, account_id)
    return Envelope(
        data=[service.transaction_view(tx, int(account.balance)) for tx in transactions],
        request_id=request_id,
    )


@router.post("/accounts/{account_id}/rebuild", response_model=Envelope[LoyaltyRebuildResponse])
async def rebuild_balance(
    account_id: UUID,
    session: SessionDep,
    context: OwnerManagerDep,
    request_id: RequestIdDep,
) -> Envelope[LoyaltyRebuildResponse]:
    """Rebuild the balance projection from the append-only ledger."""
    result = await service.rebuild_balance(session, context, account_id)
    return Envelope(data=result, request_id=request_id)

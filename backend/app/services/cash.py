from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.context import RequestContext
from app.domains.cash import CashSession, CashSessionStatus
from app.domains.payments import Payment, PaymentMethod, PaymentRefund, PaymentStatus
from app.errors import Conflict, Forbidden, NotFound
from app.models import Role, User
from app.schemas.cash import CashSessionResponse
from app.security import utc_now
from app.services.audit import record_audit

CENT = Decimal("0.01")

#: Opening and closing a till is counter work; the reports that read sessions
#: are management views, so every staff role may act here.
SESSION_ROLES = frozenset({Role.OWNER, Role.MANAGER, Role.CASHIER})


async def load_session(
    session: AsyncSession, context: RequestContext, session_id: UUID
) -> CashSession:
    cash = await session.get(CashSession, session_id)
    if cash is None or cash.organization_id != context.organization_id:
        raise NotFound("Cash session not found")
    return cash


async def current_session(
    session: AsyncSession, context: RequestContext
) -> CashSession | None:
    if context.store_id is None:
        return None
    return await session.scalar(
        select(CashSession)
        .where(
            CashSession.store_id == context.store_id,
            CashSession.status == CashSessionStatus.OPEN,
        )
        .order_by(CashSession.opened_at.desc())
        .limit(1)
    )


async def open_session(
    session: AsyncSession,
    context: RequestContext,
    opening_cash: Decimal,
    *,
    request_id: str,
) -> CashSession:
    """Open the branch's till; one open session per store at a time."""
    if context.role not in SESSION_ROLES:
        raise Forbidden("Only staff may open a cash session")
    if context.store_id is None:
        raise NotFound("Store context required")
    existing = await current_session(session, context)
    if existing is not None:
        raise Conflict("A cash session is already open for this branch")

    cash = CashSession(
        organization_id=context.organization_id,
        store_id=context.store_id,
        opened_by_user_id=context.user_id,
        opened_at=utc_now(),
        status=CashSessionStatus.OPEN,
        opening_cash=Decimal(opening_cash).quantize(CENT),
    )
    session.add(cash)
    record_audit(
        session,
        context,
        action="cash_session.opened",
        entity_type="cash_session",
        entity_id=cash.id,
        request_id=request_id,
        after={"opening_cash": str(cash.opening_cash)},
    )
    await session.commit()
    return cash


async def cash_flow(
    session: AsyncSession, store_id: UUID, start: datetime, end: datetime
) -> tuple[Decimal, Decimal]:
    """Cash the drawer took in and paid out over ``start``..``end``.

    Sums the ledger, not a running balance, so the figure a close agrees -- or
    disagrees -- with can always be recomputed from ``payments`` and
    ``payment_refunds``. A payment's ``amount`` already nets out change: the
    customer's ``received_amount`` entered the drawer and the change left it,
    which leaves the applied amount as the drawer's true gain. Refunds leave in
    the other direction and are summed from their own rows, joined back to the
    tender so only cash actually handed back counts against the drawer.

    Offline sales sync with the server's timestamp, so a sale rung during the
    session but uploaded after close lands in the *next* session's window. That
    is the honest cut: the count happened without it, and reopening history a
    counted drawer already settled is worse than carrying it forward.
    """
    cash_in = await session.scalar(
        select(func.coalesce(func.sum(Payment.amount), 0)).where(
            Payment.store_id == store_id,
            Payment.method == PaymentMethod.CASH,
            Payment.status != PaymentStatus.FAILED,
            Payment.created_at >= start,
            Payment.created_at < end,
        )
    )
    cash_out = await session.scalar(
        select(func.coalesce(func.sum(PaymentRefund.amount), 0))
        .select_from(PaymentRefund)
        .join(Payment, Payment.id == PaymentRefund.payment_id)
        .where(
            Payment.store_id == store_id,
            Payment.method == PaymentMethod.CASH,
            PaymentRefund.created_at >= start,
            PaymentRefund.created_at < end,
        )
    )
    return (
        Decimal(cash_in or 0).quantize(CENT),
        Decimal(cash_out or 0).quantize(CENT),
    )


async def close_session(
    session: AsyncSession,
    context: RequestContext,
    session_id: UUID,
    counted_cash: Decimal,
    note: str | None,
    *,
    request_id: str,
) -> CashSession:
    if context.role not in SESSION_ROLES:
        raise Forbidden("Only staff may close a cash session")
    cash = await load_session(session, context, session_id)
    if cash.store_id != context.store_id:
        raise NotFound("Cash session not found")
    if cash.status is not CashSessionStatus.OPEN:
        raise Conflict("This cash session is already closed")

    closed_at = utc_now()
    cash_in, cash_out = await cash_flow(session, cash.store_id, cash.opened_at, closed_at)
    expected = (Decimal(cash.opening_cash) + cash_in - cash_out).quantize(CENT)
    counted = Decimal(counted_cash).quantize(CENT)

    cash.status = CashSessionStatus.CLOSED
    cash.closed_at = closed_at
    cash.closed_by_user_id = context.user_id
    cash.counted_cash = counted
    cash.expected_cash = expected
    cash.difference = (counted - expected).quantize(CENT)
    cash.closing_note = note
    cash.cash_in = cash_in
    cash.cash_out = cash_out
    record_audit(
        session,
        context,
        action="cash_session.closed",
        entity_type="cash_session",
        entity_id=cash.id,
        request_id=request_id,
        after={
            "counted_cash": str(counted),
            "expected_cash": str(expected),
            "difference": str(cash.difference),
        },
    )
    await session.commit()
    return cash


async def list_sessions(
    session: AsyncSession,
    context: RequestContext,
    *,
    limit: int = 25,
    offset: int = 0,
) -> tuple[list[CashSession], int]:
    assert context.store_id is not None
    scope = [CashSession.organization_id == context.organization_id, CashSession.store_id == context.store_id]
    total = await session.scalar(select(func.count()).select_from(CashSession).where(*scope))
    rows = list(
        await session.scalars(
            select(CashSession)
            .where(*scope)
            .order_by(CashSession.opened_at.desc(), CashSession.id)
            .limit(limit)
            .offset(offset)
        )
    )
    return rows, int(total or 0)


async def session_response(
    session: AsyncSession, context: RequestContext, cash: CashSession
) -> CashSessionResponse:
    """Wire shape with live cash figures for an open session, frozen for a closed one."""
    if cash.status is CashSessionStatus.OPEN:
        cash_in, cash_out = await cash_flow(
            session, cash.store_id, cash.opened_at, utc_now()
        )
    else:
        cash_in = Decimal(cash.cash_in or 0).quantize(CENT)
        cash_out = Decimal(cash.cash_out or 0).quantize(CENT)

    user_ids = {cash.opened_by_user_id}
    if cash.closed_by_user_id is not None:
        user_ids.add(cash.closed_by_user_id)
    names: dict[UUID, str] = {}
    for row in await session.scalars(select(User).where(User.id.in_(user_ids))):
        names[row.id] = row.display_name

    return CashSessionResponse(
        id=cash.id,
        store_id=cash.store_id,
        opened_by=cash.opened_by_user_id,
        opened_by_name=names.get(cash.opened_by_user_id, "Unknown"),
        opened_at=cash.opened_at,
        closed_at=cash.closed_at,
        closed_by=cash.closed_by_user_id,
        closed_by_name=(
            names.get(cash.closed_by_user_id) if cash.closed_by_user_id is not None else None
        ),
        status=cash.status.value,
        opening_cash=Decimal(cash.opening_cash),
        counted_cash=None if cash.counted_cash is None else Decimal(cash.counted_cash),
        expected_cash=None if cash.expected_cash is None else Decimal(cash.expected_cash),
        difference=None if cash.difference is None else Decimal(cash.difference),
        closing_note=cash.closing_note,
        cash_in=cash_in,
        cash_out=cash_out,
    )

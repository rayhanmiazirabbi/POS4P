from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.context import RequestContext
from app.domains.loyalty import (
    LoyaltyAccount,
    LoyaltyTransaction,
    LoyaltyTransactionType,
)
from app.errors import Conflict, Forbidden, NotFound, ValidationError
from app.models import Role
from app.schemas.loyalty import (
    LoyaltyAccountResponse,
    LoyaltyBalanceResponse,
    LoyaltyRebuildResponse,
    LoyaltyTransactionRequest,
    LoyaltyTransactionResponse,
)
from app.security import utc_now
from app.services.audit import record_audit, redact
from app.services.customers import load_customer

#: Earning is a till action; corrections to the ledger are a management decision.
LOYALTY_EARN_ROLES = frozenset({Role.OWNER, Role.MANAGER, Role.CASHIER})
LOYALTY_ADJUST_ROLES = frozenset({Role.OWNER, Role.MANAGER})

#: Transaction types a cashier may post; anything else escalates.
EARNING_TYPES = frozenset(
    {
        LoyaltyTransactionType.EARN,
        LoyaltyTransactionType.REDEEM,
        LoyaltyTransactionType.REFUND,
        LoyaltyTransactionType.BONUS,
    }
)

#: Signed point effect per transaction type; the ledger stores signed points so a
#: rebuild is a plain sum, and the sign convention lives in exactly one place.
POINT_SIGNS: dict[LoyaltyTransactionType, int] = {
    LoyaltyTransactionType.EARN: 1,
    LoyaltyTransactionType.BONUS: 1,
    LoyaltyTransactionType.REDEEM: -1,
    LoyaltyTransactionType.REFUND: 1,
    LoyaltyTransactionType.ADJUST: 1,
    LoyaltyTransactionType.EXPIRE: -1,
}


def assert_loyalty_role(context: RequestContext, tx_type: LoyaltyTransactionType) -> None:
    if tx_type in EARNING_TYPES:
        if context.role not in LOYALTY_EARN_ROLES:
            raise Forbidden("Loyalty capability denied")
        return
    if context.role not in LOYALTY_ADJUST_ROLES:
        raise Forbidden("Loyalty adjustment requires owner or manager")


async def load_account(
    session: AsyncSession, context: RequestContext, account_id: UUID
) -> LoyaltyAccount:
    """An account of another tenant (or an unenrolled id) does not exist for the caller."""
    account = await session.get(LoyaltyAccount, account_id)
    if account is None or account.organization_id != context.organization_id:
        raise NotFound("Loyalty account not found")
    return account


async def enroll_customer(
    session: AsyncSession,
    context: RequestContext,
    customer_id: UUID,
    *,
    request_id: str,
) -> LoyaltyAccount:
    await load_customer(session, context, customer_id)
    existing = await session.scalar(
        select(LoyaltyAccount).where(
            LoyaltyAccount.organization_id == context.organization_id,
            LoyaltyAccount.customer_id == customer_id,
        )
    )
    if existing is not None:
        return existing
    account = LoyaltyAccount(
        organization_id=context.organization_id,
        customer_id=customer_id,
        balance=0,
        active=True,
    )
    session.add(account)
    try:
        await session.flush()
    except IntegrityError as exc:
        # Lost the enrollment race against another till: one account per customer.
        await session.rollback()
        existing = await session.scalar(
            select(LoyaltyAccount).where(
                LoyaltyAccount.organization_id == context.organization_id,
                LoyaltyAccount.customer_id == customer_id,
            )
        )
        if existing is None:
            raise Conflict("Loyalty account already exists") from exc
        return existing
    record_audit(
        session,
        context,
        action="loyalty.enrolled",
        entity_type="loyalty_account",
        entity_id=account.id,
        request_id=request_id,
        after=redact({"customer_id": str(customer_id)}),
    )
    await session.commit()
    await session.refresh(account)
    return account


async def get_account_by_customer(
    session: AsyncSession, context: RequestContext, customer_id: UUID
) -> LoyaltyAccount:
    await load_customer(session, context, customer_id)
    account = await session.scalar(
        select(LoyaltyAccount).where(
            LoyaltyAccount.organization_id == context.organization_id,
            LoyaltyAccount.customer_id == customer_id,
        )
    )
    if account is None:
        raise NotFound("Loyalty account not found")
    return account


async def apply_transaction(
    session: AsyncSession,
    context: RequestContext,
    account_id: UUID,
    payload: LoyaltyTransactionRequest,
    *,
    idempotency_key: str,
    request_id: str,
) -> tuple[LoyaltyTransaction, LoyaltyAccount]:
    """Append one signed ledger row and update the balance projection atomically.

    Idempotent on the organization-scoped idempotency key: a repeated key returns
    the original transaction without double-crediting. Concurrent redemption is
    serialized by the database -- the guard runs inside the same transaction as
    the writes, and a lost race rolls back whole rather than going negative.
    """
    try:
        tx_type = LoyaltyTransactionType(payload.transaction_type)
    except ValueError as exc:
        raise ValidationError("Unknown loyalty transaction type") from exc
    assert_loyalty_role(context, tx_type)
    if payload.points <= 0:
        raise ValidationError("Points must be positive")
    if tx_type in (LoyaltyTransactionType.EXPIRE, LoyaltyTransactionType.ADJUST):
        raise ValidationError("Post adjustments through the adjustment endpoint")

    account = await load_account(session, context, account_id)
    if not account.active:
        raise ValidationError("Loyalty account is inactive")

    existing = await session.scalar(
        select(LoyaltyTransaction).where(
            LoyaltyTransaction.organization_id == context.organization_id,
            LoyaltyTransaction.idempotency_key == idempotency_key,
        )
    )
    if existing is not None:
        if existing.account_id != account_id:
            raise Conflict("Idempotency key already used for a different account")
        return existing, account

    delta = POINT_SIGNS[tx_type] * payload.points
    new_balance = int(account.balance) + delta
    if new_balance < 0:
        raise ValidationError("Insufficient loyalty points", code="VALIDATION_ERROR")

    transaction = LoyaltyTransaction(
        organization_id=context.organization_id,
        account_id=account.id,
        transaction_type=tx_type,
        points=delta,
        source_type=payload.source_type.strip(),
        source_id=payload.source_id,
        idempotency_key=idempotency_key,
        expires_at=payload.expires_at,
        created_at=utc_now(),
    )
    session.add(transaction)
    account.balance = new_balance
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise Conflict("Idempotency key already used") from exc
    record_audit(
        session,
        context,
        action=f"loyalty.{tx_type.value}",
        entity_type="loyalty_transaction",
        entity_id=transaction.id,
        request_id=request_id,
        after=redact(
            {
                "account_id": str(account.id),
                "points": str(delta),
                "balance": str(new_balance),
                "source_type": transaction.source_type,
            }
        ),
    )
    await session.commit()
    await session.refresh(account)
    return transaction, account


async def apply_adjustment(
    session: AsyncSession,
    context: RequestContext,
    account_id: UUID,
    payload: LoyaltyTransactionRequest,
    *,
    idempotency_key: str,
    request_id: str,
) -> tuple[LoyaltyTransaction, LoyaltyAccount]:
    """Owner/manager correction with an explicit signed delta."""
    if payload.transaction_type != "adjust":
        raise ValidationError("Not an adjustment")
    assert_loyalty_role(context, LoyaltyTransactionType.ADJUST)
    if payload.points == 0:
        raise ValidationError("Adjustment must be non-zero")
    account = await load_account(session, context, account_id)
    if not account.active:
        raise ValidationError("Loyalty account is inactive")

    existing = await session.scalar(
        select(LoyaltyTransaction).where(
            LoyaltyTransaction.organization_id == context.organization_id,
            LoyaltyTransaction.idempotency_key == idempotency_key,
        )
    )
    if existing is not None:
        if existing.account_id != account_id:
            raise Conflict("Idempotency key already used for a different account")
        return existing, account

    delta = payload.points
    new_balance = int(account.balance) + delta
    if new_balance < 0:
        raise ValidationError("Insufficient loyalty points")

    transaction = LoyaltyTransaction(
        organization_id=context.organization_id,
        account_id=account.id,
        transaction_type=LoyaltyTransactionType.ADJUST,
        points=delta,
        source_type=payload.source_type.strip() or "manual",
        source_id=payload.source_id,
        idempotency_key=idempotency_key,
        expires_at=None,
        created_at=utc_now(),
    )
    session.add(transaction)
    account.balance = new_balance
    record_audit(
        session,
        context,
        action="loyalty.adjust",
        entity_type="loyalty_transaction",
        entity_id=transaction.id,
        request_id=request_id,
        before={"balance": str(int(account.balance) - delta)},
        after=redact({"points": str(delta), "balance": str(new_balance)}),
    )
    await session.commit()
    await session.refresh(account)
    return transaction, account


async def expire_due_points(
    session: AsyncSession,
    context: RequestContext,
    account_id: UUID,
    *,
    as_of: datetime | None = None,
    request_id: str = "unknown",
) -> list[LoyaltyTransaction]:
    """Post EXPIRE rows for every earn/bonus lot whose deadline has passed.

    Expiry never drives the balance below zero beyond what those lots credited:
    redemptions consume FIFO from the oldest lots first, so expired remainder is
    what is actually left of them.
    """
    assert_loyalty_role(context, LoyaltyTransactionType.EXPIRE)
    account = await load_account(session, context, account_id)
    now = as_of or utc_now()
    ledger = list(
        await session.scalars(
            select(LoyaltyTransaction)
            .where(LoyaltyTransaction.account_id == account.id)
            .order_by(LoyaltyTransaction.created_at, LoyaltyTransaction.id)
        )
    )
    already_expired = {str(tx.source_id) for tx in ledger if tx.source_type == "expiry"}
    # Redemptions consume FIFO from the oldest lot first; whatever remains of a
    # lapsed lot after that consumption is what can still expire.
    outstanding_redemptions = -sum(int(tx.points) for tx in ledger if tx.points < 0 and tx.source_type != "expiry")
    balance = int(account.balance)
    transactions: list[LoyaltyTransaction] = []
    for lot in ledger:
        expires_at = lot.expires_at
        if expires_at is not None and expires_at.tzinfo is None:
            # SQLite hands naive datetimes back; the comparison clock is UTC.
            from datetime import UTC as _UTC

            expires_at = expires_at.replace(tzinfo=_UTC)
        if lot.points <= 0 or expires_at is None or expires_at > now:
            continue
        if str(lot.id) in already_expired:
            continue
        remaining = int(lot.points) - outstanding_redemptions
        outstanding_redemptions = max(-remaining, 0) if remaining < 0 else 0
        if remaining <= 0:
            continue
        expiring = min(remaining, balance)
        if expiring <= 0:
            break
        transaction = LoyaltyTransaction(
            organization_id=context.organization_id,
            account_id=account.id,
            transaction_type=LoyaltyTransactionType.EXPIRE,
            points=-expiring,
            source_type="expiry",
            source_id=lot.id,
            idempotency_key=f"expire:{lot.id}",
            expires_at=None,
            created_at=now,
        )
        session.add(transaction)
        transactions.append(transaction)
        balance -= expiring
    account.balance = balance
    if transactions:
        record_audit(
            session,
            context,
            action="loyalty.expired",
            entity_type="loyalty_account",
            entity_id=account.id,
            request_id=request_id,
            after=redact({"expired_lots": len(transactions), "balance": str(int(account.balance))}),
        )
        await session.commit()
        await session.refresh(account)
    return transactions


async def rebuild_balance(
    session: AsyncSession,
    context: RequestContext,
    account_id: UUID,
    *,
    commit: bool = True,
) -> LoyaltyRebuildResponse:
    """Recompute the balance projection from the append-only ledger."""
    account = await load_account(session, context, account_id)
    total = int(
        await session.scalar(
            select(func.coalesce(func.sum(LoyaltyTransaction.points), 0)).where(
                LoyaltyTransaction.account_id == account.id
            )
        )
        or 0
    )
    previous = int(account.balance)
    account.balance = total
    if total != previous:
        record_audit(
            session,
            context,
            request_id="loyalty-rebuild",
            action="loyalty.balance_rebuilt",
            entity_type="loyalty_account",
            entity_id=account.id,
            before={"balance": str(previous)},
            after=redact({"balance": str(total)}),
        )
    if commit:
        await session.commit()
        await session.refresh(account)
    return LoyaltyRebuildResponse(
        account=LoyaltyAccountResponse.model_validate(account),
        ledger_total=total,
    )


def transaction_view(
    transaction: LoyaltyTransaction, balance_after: int | None = None
) -> LoyaltyTransactionResponse:
    return LoyaltyTransactionResponse(
        id=transaction.id,
        account_id=transaction.account_id,
        transaction_type=str(transaction.transaction_type.value)
        if hasattr(transaction.transaction_type, "value")
        else str(transaction.transaction_type),
        points=int(transaction.points),
        balance_after=balance_after,
        source_type=transaction.source_type,
        source_id=transaction.source_id,
        expires_at=transaction.expires_at,
        created_at=transaction.created_at,
    )


def balance_response(account: LoyaltyAccount) -> LoyaltyBalanceResponse:
    return LoyaltyBalanceResponse(
        account=LoyaltyAccountResponse.model_validate(account),
    )

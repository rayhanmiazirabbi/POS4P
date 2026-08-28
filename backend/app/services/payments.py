from __future__ import annotations

from decimal import Decimal
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.context import RequestContext
from app.domains.customers import Customer
from app.domains.payments import Payment, PaymentMethod, PaymentRefund, PaymentStatus
from app.errors import Conflict, NotFound, ValidationError
from app.models import Organization
from app.schemas.organizations import OrganizationSettings
from app.security import utc_now
from app.services.audit import record_audit

CENT = Decimal("0.01")

#: Credit is cancelled before cash leaves the drawer, and mobile wallets before
#: cash too -- reversing a wallet transfer costs nothing, while cash handed back
#: cannot be recalled. Refunding in this order keeps the till whole and leaves the
#: customer no worse off, since the debt they no longer owe is worth the same as
#: the note they would have been handed.
REFUND_PRIORITY: tuple[str, ...] = (
    PaymentMethod.DUE.value,
    PaymentMethod.BKASH.value,
    PaymentMethod.NAGAD.value,
    PaymentMethod.CASH.value,
)


async def allowed_payment_methods(session: AsyncSession, context: RequestContext) -> set[str]:
    """Every tender this organization may book right now.

    Built-ins plus the tenant's configured digital methods: a payment against a
    method the org never configured is a typo or a stale terminal, and the sooner
    it is refused the fewer books have to be unwound.
    """
    organization = await session.get(Organization, context.organization_id)
    settings = (
        OrganizationSettings.model_validate(organization.settings)
        if organization is not None and organization.settings
        else OrganizationSettings()
    )
    return {PaymentMethod.CASH.value, PaymentMethod.DUE.value} | {
        method.value for method in settings.payment_methods if method.active
    }


async def load_payment(
    session: AsyncSession, context: RequestContext, payment_id: UUID
) -> Payment:
    """A payment of another tenant (or branch) does not exist for the caller."""
    payment = await session.get(Payment, payment_id)
    if payment is None or payment.organization_id != context.organization_id:
        raise NotFound("Payment not found")
    if context.store_id is not None and payment.store_id != context.store_id:
        raise NotFound("Payment not found")
    return payment


def change_due(payment: Payment) -> Decimal:
    """Cash change owed to the customer: received minus charged."""
    if payment.method != PaymentMethod.CASH.value or payment.received_amount is None:
        return Decimal("0.00")
    return (Decimal(payment.received_amount) - Decimal(payment.amount)).quantize(
        Decimal("0.01")
    )


async def refunded_amounts(
    session: AsyncSession, payment_ids: list[UUID]
) -> dict[UUID, Decimal]:
    """How much has already been refunded against each payment.

    Returns are partial and repeatable, so every refund has to be measured
    against what the ledger already holds rather than against the sale total.
    """
    if not payment_ids:
        return {}
    rows = await session.execute(
        select(PaymentRefund.payment_id, func.sum(PaymentRefund.amount))
        .where(PaymentRefund.payment_id.in_(payment_ids))
        .group_by(PaymentRefund.payment_id)
    )
    return {payment_id: Decimal(total or 0) for payment_id, total in rows.all()}


async def refund_payments(
    session: AsyncSession,
    context: RequestContext,
    payments: list[Payment],
    amount: Decimal,
    *,
    reason: str,
    request_id: str,
    key_prefix: str,
) -> list[PaymentRefund]:
    """Refund ``amount`` across a sale's tenders, writing one ledger row each.

    Splitting the refund across the original tenders -- rather than assuming one
    method -- is what makes ``customers.due_balance`` and the daily payment
    breakdown recomputable from the ledger instead of merely maintained. Each
    tender is capped by what it still has left to give, so refunding twice can
    never return more than was taken.

    ``due`` rows also write the balance down, because that is the same money: the
    debt is cancelled instead of cash being handed over.

    ``key_prefix`` names the correction being paid back (``return:<id>`` or
    ``void:<id>``), which makes each refund row's key a function of its cause
    rather than a fresh UUID. A random key made the unique constraint on
    ``payment_refunds`` unenforceable: two refunds of the same money looked like
    two different refunds, so a retry that got past the caller wrote both.
    """
    remaining = Decimal(amount).quantize(CENT)
    if remaining <= 0:
        return []
    already = await refunded_amounts(session, [payment.id for payment in payments])
    refunds: list[PaymentRefund] = []
    now = utc_now()

    # Tenant-configured methods are not in REFUND_PRIORITY, so a wildcard pass
    # sits where they all belong: after due, before the named wallets and cash.
    # A configured wallet reverses as cheaply as a named one.
    known = set(REFUND_PRIORITY)
    order: list[str | None] = [PaymentMethod.DUE.value, None, *REFUND_PRIORITY[1:]]
    for method in order:
        for payment in payments:
            if remaining <= 0:
                break
            matches = (
                payment.method not in known if method is None else payment.method == method
            )
            if not matches or payment.status is not PaymentStatus.CAPTURED:
                continue
            available = Decimal(payment.amount) - already.get(payment.id, Decimal(0))
            take = min(remaining, available).quantize(CENT)
            if take <= 0:
                continue
            refund = PaymentRefund(
                organization_id=payment.organization_id,
                store_id=payment.store_id,
                payment_id=payment.id,
                amount=take,
                idempotency_key=f"{key_prefix}:{payment.id}",
                created_at=now,
            )
            session.add(refund)
            refunds.append(refund)
            remaining -= take

            if payment.method == PaymentMethod.DUE.value and payment.customer_id is not None:
                customer = await session.get(Customer, payment.customer_id)
                if customer is not None:
                    customer.due_balance = max(
                        Decimal(customer.due_balance) - take, Decimal(0)
                    ).quantize(CENT)

    if remaining > 0:
        # The tenders could not absorb the whole refund, so the customer is owed
        # money this ledger has no row for. Silently returning the short amount
        # was worse than failing: the caller went on to commit a return whose
        # ``total`` claimed a refund larger than the sum of its refund rows, so
        # the sale's own books disagreed about what was paid back and no report
        # could tell which figure was right. A refund that cannot be booked in
        # full is a conflict for a human to resolve.
        raise Conflict(
            f"Refund of {Decimal(amount).quantize(CENT)} exceeds the refundable "
            f"balance of this sale's tenders by {remaining}"
        )

    if refunds:
        await session.flush()
        record_audit(
            session,
            context,
            action="payment.refunded",
            entity_type="payment",
            entity_id=refunds[0].payment_id,
            request_id=request_id,
            after={
                "reason": reason,
                "total": str(sum((Decimal(r.amount) for r in refunds), Decimal(0))),
                "refund_ids": [str(r.id) for r in refunds],
            },
        )
    return refunds


async def create_sale_payment(
    session: AsyncSession,
    context: RequestContext,
    *,
    sale,
    method: str,
    amount: Decimal,
    received_amount: Decimal | None = None,
    provider_reference: str | None = None,
    idempotency_key: str,
    request_id: str,
) -> tuple[Payment, Decimal]:
    """Record one tender against a sale; returns the payment and its cash change.

    ``due`` tenders book the debt against the sale's customer. No commit happens
    here so the sales service can compose every row into one transaction.
    """
    method = str(method).strip().lower()
    if method not in await allowed_payment_methods(session, context):
        raise ValidationError(f"Payment method '{method}' is not configured for this organization")
    amount = Decimal(amount).quantize(Decimal("0.01"))
    if amount < 0:
        raise ValidationError("Payment amount cannot be negative")
    if received_amount is not None:
        received = Decimal(received_amount).quantize(Decimal("0.01"))
    else:
        received = None
    if method == PaymentMethod.CASH.value:
        if received is None:
            raise ValidationError("Cash payments require a received amount")
        if received < amount:
            raise Conflict("Cash received cannot be less than the amount due")
    elif received is not None and received != amount:
        raise Conflict("Only cash payments may carry change")

    customer_id = sale.customer_id
    if method == PaymentMethod.DUE.value:
        if customer_id is None:
            raise ValidationError("Due payments require a customer on the sale")
        customer = await session.get(Customer, customer_id)
        if customer is None:
            raise NotFound("Customer not found")
        customer.due_balance = Decimal(customer.due_balance) + amount

    payment = Payment(
        organization_id=context.organization_id,
        store_id=sale.store_id,
        reference_type="sale",
        reference_id=sale.id,
        customer_id=customer_id,
        method=method,
        amount=amount,
        received_amount=received,
        status=PaymentStatus.CAPTURED,
        provider_reference=provider_reference,
        idempotency_key=idempotency_key,
        created_at=utc_now(),
    )
    session.add(payment)
    await session.flush()
    record_audit(
        session,
        context,
        action="payment.created",
        entity_type="payment",
        entity_id=payment.id,
        request_id=request_id,
        after={"method": method, "amount": str(amount), "sale_id": str(sale.id)},
    )
    return payment, change_due(payment)


ALLOWED_STATUS_TRANSITIONS: dict[PaymentStatus, frozenset[PaymentStatus]] = {
    PaymentStatus.PENDING: frozenset({PaymentStatus.CAPTURED, PaymentStatus.FAILED}),
}


async def update_payment_status(
    session: AsyncSession,
    context: RequestContext,
    payment_id: UUID,
    new_status: PaymentStatus,
    *,
    provider_reference: str | None = None,
    request_id: str,
) -> Payment:
    """Manual status correction; only pending payments may be resolved by hand."""
    new_status = PaymentStatus(new_status)  # pydantic delivers enum fields as values
    payment = await load_payment(session, context, payment_id)
    current = PaymentStatus(payment.status)
    allowed = ALLOWED_STATUS_TRANSITIONS.get(current, frozenset())
    if new_status not in allowed:
        raise Conflict(f"Cannot move a {current.value} payment to {new_status.value}")
    before = {"status": current.value}
    payment.status = new_status
    if provider_reference is not None:
        payment.provider_reference = provider_reference
    record_audit(
        session,
        context,
        action="payment.status_updated",
        entity_type="payment",
        entity_id=payment.id,
        request_id=request_id,
        before=before,
        after={"status": new_status.value},
    )
    await session.commit()
    return payment


async def list_payments(
    session: AsyncSession,
    context: RequestContext,
    *,
    reference_type: str | None = None,
    reference_id: UUID | None = None,
    customer_id: UUID | None = None,
    method: str | None = None,
    status: PaymentStatus | None = None,
    limit: int = 25,
    offset: int = 0,
) -> tuple[list[Payment], int]:
    scope: list = [Payment.organization_id == context.organization_id]
    if context.store_id is not None:
        scope.append(Payment.store_id == context.store_id)
    if reference_type is not None:
        scope.append(Payment.reference_type == reference_type)
    if reference_id is not None:
        scope.append(Payment.reference_id == reference_id)
    if customer_id is not None:
        scope.append(Payment.customer_id == customer_id)
    if method is not None:
        scope.append(Payment.method == method)
    if status is not None:
        scope.append(Payment.status == status)
    total = await session.scalar(select(func.count()).select_from(Payment).where(*scope))
    rows = list(
        await session.scalars(
            select(Payment)
            .where(*scope)
            .order_by(Payment.created_at.desc(), Payment.id)
            .limit(limit)
            .offset(offset)
        )
    )
    return rows, int(total or 0)

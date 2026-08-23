from __future__ import annotations

from decimal import Decimal
from re import fullmatch
from typing import Any
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.context import RequestContext
from app.domains.customers import Customer, CustomerAddress
from app.domains.payments import Payment, PaymentMethod, PaymentRefund, PaymentStatus
from app.domains.sales import Sale, SaleReturn, SaleStatus
from app.errors import Conflict, NotFound, ValidationError
from app.models import Role
from app.schemas.customers import (
    CustomerAddressCreate,
    CustomerCreate,
    CustomerHistorySummary,
    CustomerUpdate,
)
from app.services.audit import record_audit

# Bangladesh mobile numbers normalize to +8801XXXXXXXXX (11 significant digits).
_BD_MOBILE = r"^(?:\+?880|0)?1[3-9]\d{8}$"

CENT = Decimal("0.01")

#: Lifetime spend profiles a customer and answers no question a till needs, so it
#: is owner/manager only. The outstanding due is different: a cashier cannot take
#: a payment against a balance they are not allowed to see, so it stays visible.
SPEND_VISIBLE_ROLES = frozenset({Role.OWNER, Role.MANAGER})


def can_see_lifetime_spend(context: RequestContext) -> bool:
    return context.role in SPEND_VISIBLE_ROLES


def normalize_phone(raw: str | None) -> str | None:
    """Normalize a Bangladeshi mobile number to +8801XXXXXXXXX.

    Spaces, dashes, parentheses, and leading ``0``/``+880`` variants all collapse
    onto the same canonical form. Blank input means "no phone" (walk-in guest).
    """
    if raw is None:
        return None
    digits = "".join(ch for ch in raw if ch.isdigit())
    if not digits:
        if raw.strip():
            raise ValidationError("Phone number contains no digits")
        return None
    if not fullmatch(_BD_MOBILE, digits):
        return None
    return f"+880{digits[-10:]}"


async def load_customer(
    session: AsyncSession, context: RequestContext, customer_id: UUID
) -> Customer:
    """A customer of another tenant does not exist for the caller."""
    customer = await session.get(Customer, customer_id)
    if customer is None or customer.organization_id != context.organization_id:
        raise NotFound("Customer not found")
    return customer


async def _find_by_phone(
    session: AsyncSession, organization_id: UUID, phone: str
) -> Customer | None:
    """The pre-check behind the friendly duplicate message.

    Only advisory: two tills can both pass it before either inserts, so every
    caller must still handle the unique violation that decides the race.
    """
    return await session.scalar(
        select(Customer).where(
            Customer.organization_id == organization_id,
            Customer.normalized_phone == phone,
        )
    )


async def create_customer(
    session: AsyncSession,
    context: RequestContext,
    payload: CustomerCreate,
    *,
    request_id: str,
) -> Customer:
    normalized = payload.normalized_phone
    if normalized is not None:
        existing = await _find_by_phone(session, context.organization_id, normalized)
        if existing is not None:
            raise Conflict("A customer with this phone number already exists")
    customer = Customer(
        organization_id=context.organization_id,
        name=payload.name.strip(),
        normalized_phone=normalized,
        email=payload.email,
        preferences=payload.preferences or {},
    )
    session.add(customer)
    try:
        await session.flush()
        record_audit(
            session,
            context,
            action="customer.created",
            entity_type="customer",
            entity_id=customer.id,
            request_id=request_id,
            after={"name": customer.name, "phone": customer.normalized_phone},
        )
        await session.commit()
    except IntegrityError as exc:
        # Lost the insert race: the unique index, not the pre-check, is authoritative.
        await session.rollback()
        raise Conflict("A customer with this phone number already exists") from exc
    except Exception:
        await session.rollback()
        raise
    return customer


async def update_customer(
    session: AsyncSession,
    context: RequestContext,
    customer_id: UUID,
    payload: CustomerUpdate,
    *,
    request_id: str,
) -> Customer:
    customer = await load_customer(session, context, customer_id)
    # Snapshot before mutating: an audit trail whose before and after agree cannot
    # answer the one question it exists to answer.
    before = {
        "name": customer.name,
        "email": customer.email,
        "phone": customer.normalized_phone,
    }
    if payload.name is not None:
        customer.name = payload.name.strip()
    if payload.email is not None:
        customer.email = payload.email
    if payload.preferences is not None:
        customer.preferences = payload.preferences
    if payload.normalized_phone is not None:
        clash = await _find_by_phone(session, context.organization_id, payload.normalized_phone)
        if clash is not None and clash.id != customer.id:
            raise Conflict("A customer with this phone number already exists")
        customer.normalized_phone = payload.normalized_phone

    after = {
        "name": customer.name,
        "email": customer.email,
        "phone": customer.normalized_phone,
    }
    try:
        record_audit(
            session,
            context,
            action="customer.updated",
            entity_type="customer",
            entity_id=customer.id,
            request_id=request_id,
            before=before,
            after=after,
        )
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise Conflict("A customer with this phone number already exists") from exc
    except Exception:
        await session.rollback()
        raise
    return customer


async def deactivate_customer(
    session: AsyncSession,
    context: RequestContext,
    customer_id: UUID,
    *,
    request_id: str,
) -> Customer:
    customer = await load_customer(session, context, customer_id)
    customer.active = False
    record_audit(
        session,
        context,
        action="customer.deactivated",
        entity_type="customer",
        entity_id=customer.id,
        request_id=request_id,
    )
    await session.commit()
    return customer


async def search_customers(
    session: AsyncSession,
    context: RequestContext,
    *,
    q: str | None = None,
    active: bool | None = True,
    has_due: bool | None = None,
    limit: int = 25,
    offset: int = 0,
) -> tuple[list[Customer], int]:
    """Search within the tenant, active customers only unless asked otherwise.

    A deactivated customer must not be selectable at the till, but the row is
    never orphaned -- ``active=false`` still reaches it so it can be reviewed.

    ``has_due`` is how a shop finds who to chase for credit, so it filters on the
    balance rather than merely sorting by it.
    """
    scope: tuple[Any, ...] = (Customer.organization_id == context.organization_id,)
    if active is not None:
        scope = (*scope, Customer.active.is_(active))
    if has_due is not None:
        scope = (
            *scope,
            Customer.due_balance > 0 if has_due else Customer.due_balance <= 0,
        )
    if q:
        query = q.strip()
        phone_digits = "".join(ch for ch in query if ch.isdigit())
        conditions: list[Any] = [Customer.name.ilike(f"{query}%")]
        if phone_digits:
            conditions.append(Customer.normalized_phone.like(f"%{phone_digits}%"))
        scope = (*scope, or_(*conditions))
    total = await session.scalar(select(func.count()).select_from(Customer).where(*scope))
    rows = list(
        await session.scalars(
            select(Customer)
            .where(*scope)
            .order_by(Customer.created_at.desc(), Customer.id)
            .limit(limit)
            .offset(offset)
        )
    )
    return rows, int(total or 0)


async def get_history_summary(
    session: AsyncSession,
    context: RequestContext,
    customer_id: UUID,
) -> CustomerHistorySummary:
    """Purchase history for one customer, netted and scoped to the caller's tenant.

    ``sales.customer_id`` has no organization in its foreign key, so nothing stops
    another tenant's row from naming this id -- every aggregate is scoped rather
    than trusting the id alone.

    Spend is net of returns: gross would overstate a refunded customer for the
    life of the account.
    """
    customer = await load_customer(session, context, customer_id)
    scope = (
        Sale.organization_id == context.organization_id,
        Sale.customer_id == customer_id,
        Sale.status == SaleStatus.COMPLETED,
    )
    sale_count = await session.scalar(select(func.count()).select_from(Sale).where(*scope))
    gross = Decimal(
        await session.scalar(select(func.coalesce(func.sum(Sale.total), 0)).where(*scope)) or 0
    )
    # ``sale_returns.total`` is stored negative so the ledger sums to net; the sign
    # is flipped once, here, because a refund line reads better as a positive.
    refunded = abs(
        Decimal(
            await session.scalar(
                select(func.coalesce(func.sum(SaleReturn.total), 0))
                .select_from(SaleReturn)
                .join(Sale, Sale.id == SaleReturn.sale_id)
                .where(*scope)
            )
            or 0
        )
    )
    return CustomerHistorySummary(
        customer_id=customer_id,
        sale_count=int(sale_count or 0),
        total_spent=(gross - refunded).quantize(CENT),
        total_refunded=refunded.quantize(CENT),
        total_due=Decimal(customer.due_balance).quantize(CENT),
    )


async def due_balance_from_ledger(
    session: AsyncSession, context: RequestContext, customer_id: UUID
) -> Decimal:
    """Recompute what a customer owes from the payment ledger alone.

    Credit is taken on as a ``due`` tender and cancelled by a refund against that
    tender, so the balance is fully derivable -- which is what cross-cutting rule 2
    requires of a projection. Voided sales are excluded because voiding refunds
    their tenders in full, and counting both would double-cancel the debt.
    """
    taken = Decimal(
        await session.scalar(
            select(func.coalesce(func.sum(Payment.amount), 0))
            .select_from(Payment)
            .where(
                Payment.organization_id == context.organization_id,
                Payment.customer_id == customer_id,
                Payment.method == PaymentMethod.DUE,
                Payment.status == PaymentStatus.CAPTURED,
            )
        )
        or 0
    )
    cancelled = Decimal(
        await session.scalar(
            select(func.coalesce(func.sum(PaymentRefund.amount), 0))
            .select_from(PaymentRefund)
            .join(Payment, Payment.id == PaymentRefund.payment_id)
            .where(
                Payment.organization_id == context.organization_id,
                Payment.customer_id == customer_id,
                Payment.method == PaymentMethod.DUE,
            )
        )
        or 0
    )
    return max(taken - cancelled, Decimal(0)).quantize(CENT)


async def rebuild_due_balance(
    session: AsyncSession,
    context: RequestContext,
    customer_id: UUID,
    *,
    request_id: str,
) -> Customer:
    """Reconcile ``due_balance`` against the ledger and record what moved.

    Idempotent by construction: the ledger is the input, so a second rebuild
    computes the same figure. The audit row keeps the drift visible instead of
    letting a silent correction hide the incident that caused it.
    """
    customer = await load_customer(session, context, customer_id)
    previous = Decimal(customer.due_balance).quantize(CENT)
    rebuilt = await due_balance_from_ledger(session, context, customer_id)
    customer.due_balance = rebuilt
    record_audit(
        session,
        context,
        action="customer.due_rebuilt",
        entity_type="customer",
        entity_id=customer.id,
        request_id=request_id,
        before={"due_balance": str(previous)},
        after={"due_balance": str(rebuilt), "drift": str(rebuilt - previous)},
    )
    await session.commit()
    await session.refresh(customer)
    return customer


async def create_address(
    session: AsyncSession,
    context: RequestContext,
    customer_id: UUID,
    payload: CustomerAddressCreate,
    *,
    request_id: str,
) -> CustomerAddress:
    await load_customer(session, context, customer_id)
    address = CustomerAddress(
        organization_id=context.organization_id,
        customer_id=customer_id,
        label=payload.label.strip(),
        address_line=payload.address_line.strip(),
        city=payload.city,
        postal_code=payload.postal_code,
    )
    session.add(address)
    await session.flush()
    record_audit(
        session,
        context,
        action="customer.address_created",
        entity_type="customer_address",
        entity_id=address.id,
        request_id=request_id,
        after={"customer_id": str(customer_id), "label": address.label},
    )
    await session.commit()
    return address


async def list_addresses(
    session: AsyncSession,
    context: RequestContext,
    customer_id: UUID,
) -> list[CustomerAddress]:
    """Active addresses, scoped to the tenant as well as the customer.

    The address foreign key is not organization-scoped, so filtering on
    ``customer_id`` alone would happily serve another tenant's row.
    """
    await load_customer(session, context, customer_id)
    return list(
        await session.scalars(
            select(CustomerAddress)
            .where(
                CustomerAddress.organization_id == context.organization_id,
                CustomerAddress.customer_id == customer_id,
                CustomerAddress.active.is_(True),
            )
            .order_by(CustomerAddress.created_at.desc(), CustomerAddress.id)
        )
    )

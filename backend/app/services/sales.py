from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.context import RequestContext
from app.domains.inventory import (
    InventoryMovement,
    InventoryMovementType,
)
from app.domains.sales import (
    DiscountApproval,
    Sale,
    SaleChannel,
    SaleItem,
    SaleItemBatchAllocation,
    SaleReturn,
    SaleStatus,
)
from app.domains.sync import StoreSequence
from app.errors import Conflict, Forbidden, NotFound, ValidationError
from app.models import Organization, OrganizationUser, RecordStatus, Role, StoreProduct, User
from app.schemas.organizations import OrganizationSettings
from app.security import as_utc, generate_token, hash_token, utc_now, verify_secret
from app.services.audit import enqueue_outbox, record_audit, redact
from app.services.customers import load_customer
from app.services.idempotency import remember, replay
from app.services.loyalty import (
    find_account_for_customer,
    get_account_by_customer,
    redeem_for_sale,
    restore_for_sale,
)
from app.services.inventory import (
    InsufficientStock,
    _apply_to_balance,
    allocate_fefo_for_product,
    load_store_product,
)
from app.services.organizations import coerce_settings
from app.services.payments import create_sale_payment, refund_payments
from app.services.stores import business_date, load_store
from app.services.users import normalize_phone

STAFF_ROLES = frozenset({Role.OWNER, Role.MANAGER, Role.CASHIER})
VOID_ROLES = frozenset({Role.OWNER, Role.MANAGER})

CENT = Decimal("0.01")


@dataclass
class SaleCreated:
    sale: Sale
    items: list[SaleItem] = field(default_factory=list)
    payments: list[Any] = field(default_factory=list)
    #: The loyalty balance after this sale's redemption, when one happened; only
    #: the creating call can know it, so replays carry it inside ``replay_body``.
    loyalty_balance_after: int | None = None
    #: Set when an idempotent replay served a stored response body instead.
    replay_body: dict[str, Any] | None = None


@dataclass
class SaleReturnCreated:
    #: ``None`` on a replay: the row belongs to the first call's transaction, and
    #: reloading it would only re-derive what ``replay_body`` already holds.
    sale_return: SaleReturn | None = None
    replay_body: dict[str, Any] | None = None


async def load_sale(session: AsyncSession, context: RequestContext, sale_id: UUID) -> Sale:
    """A sale of another tenant (or branch) does not exist for the caller."""
    sale = await session.get(Sale, sale_id)
    if sale is None or sale.organization_id != context.organization_id:
        raise NotFound("Sale not found")
    if context.store_id is not None and sale.store_id != context.store_id:
        raise NotFound("Sale not found")
    return sale


async def _load_items(session: AsyncSession, sale_id: UUID) -> list[SaleItem]:
    return list(
        await session.scalars(
            select(SaleItem).where(SaleItem.sale_id == sale_id).order_by(SaleItem.id)
        )
    )


async def _load_payments(session: AsyncSession, sale_id: UUID) -> list[Any]:
    from app.domains.payments import Payment

    return list(
        await session.scalars(
            select(Payment)
            .where(Payment.reference_type == "sale", Payment.reference_id == sale_id)
            .order_by(Payment.created_at, Payment.id)
        )
    )


async def _next_receipt_number(session: AsyncSession, store_id: UUID) -> str:
    """Gapless per-store receipt number.

    Locks the counter row so two tills selling at the same moment cannot read the
    same value and print the same receipt number -- a duplicate number is worse
    than a gap, because two different sales then reconcile to one entry.

    The lock is a no-op on SQLite (the dialect omits ``FOR UPDATE``), which is what
    the tests run on; it is the row lock that matters on PostgreSQL.
    """
    sequence = await session.get(StoreSequence, store_id, with_for_update=True)
    if sequence is None:
        sequence = StoreSequence(store_id=store_id, last_sequence=0, last_receipt_sequence=0)
        session.add(sequence)
        await session.flush()
    sequence.last_receipt_sequence += 1
    await session.flush()
    return f"R-{sequence.last_receipt_sequence:08d}"


def compute_totals(lines: list[tuple[Decimal, Decimal]], discount: Decimal) -> tuple[Decimal, Decimal, Decimal]:
    """Server-side totals: unit prices come from store_products, never the client."""
    subtotal = sum((quantity * price for quantity, price in lines), Decimal(0)).quantize(CENT)
    discount = Decimal(discount).quantize(CENT)
    total = subtotal - discount
    if total < 0:
        raise ValidationError("Discount cannot exceed the sale subtotal")
    return subtotal, discount, total


def _discount_amount(base: Decimal, discount: Any | None) -> Decimal:
    if discount is None or Decimal(discount.value) == 0:
        return Decimal("0.00")
    value = Decimal(discount.value)
    if discount.mode == "percentage":
        if value > 100:
            raise ValidationError("Percentage discount cannot exceed 100")
        amount = (base * value / Decimal(100)).quantize(CENT, rounding=ROUND_HALF_UP)
    else:
        amount = value.quantize(CENT)
    if amount > base:
        raise ValidationError("Flat discount cannot exceed its eligible subtotal")
    return amount


@dataclass(frozen=True)
class StructuredTotals:
    subtotal: Decimal
    line_discounts: list[Decimal]
    line_discount: Decimal
    global_discount: Decimal
    delivery_charge: Decimal
    other_fee: Decimal
    other_fee_label: str | None
    total: Decimal


def structured_totals(payload: Any, products: list[StoreProduct]) -> StructuredTotals:
    gross = [
        (Decimal(line.quantity) * Decimal(product.sale_price)).quantize(CENT)
        for line, product in zip(payload.items, products)
    ]
    line_discounts = [
        _discount_amount(line_total, line.discount)
        for line_total, line in zip(gross, payload.items)
    ]
    after_lines = sum(
        (line_total - discount for line_total, discount in zip(gross, line_discounts)),
        Decimal(0),
    ).quantize(CENT)
    if payload.global_discount is not None:
        global_discount = _discount_amount(after_lines, payload.global_discount)
    else:
        global_discount = Decimal(payload.discount).quantize(CENT)
        if global_discount > after_lines:
            raise ValidationError("Discount cannot exceed the sale subtotal")

    delivery = Decimal(0)
    other = Decimal(0)
    other_label: str | None = None
    seen: set[str] = set()
    for charge in payload.charges:
        if charge.kind in seen:
            raise ValidationError(f"Only one {charge.kind} charge is allowed")
        seen.add(charge.kind)
        if charge.kind == "delivery":
            delivery = Decimal(charge.amount).quantize(CENT)
        else:
            other = Decimal(charge.amount).quantize(CENT)
            other_label = charge.label
    total = (after_lines - global_discount + delivery + other).quantize(CENT)
    return StructuredTotals(
        subtotal=sum(gross, Decimal(0)).quantize(CENT),
        line_discounts=line_discounts,
        line_discount=sum(line_discounts, Decimal(0)).quantize(CENT),
        global_discount=global_discount,
        delivery_charge=delivery,
        other_fee=other,
        other_fee_label=other_label,
        total=total,
    )


def discount_fingerprint(value: Any) -> str:
    data = value.model_dump(
        by_alias=True,
        mode="json",
        include={"items", "discount", "global_discount", "charges"},
    )
    return hashlib.sha256(json.dumps(data, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


async def approve_discount(session: AsyncSession, context: RequestContext, payload: Any) -> tuple[str, DiscountApproval, User]:
    if context.store_id is None:
        raise ValidationError("Store context required", code="STORE_CONTEXT_REQUIRED")
    user = await session.scalar(
        select(User)
        .join(OrganizationUser, OrganizationUser.user_id == User.id)
        .where(
            User.phone == normalize_phone(payload.phone),
            User.status == RecordStatus.ACTIVE,
            OrganizationUser.organization_id == context.organization_id,
            OrganizationUser.active.is_(True),
            OrganizationUser.role.in_((Role.OWNER, Role.MANAGER)),
        )
    )
    if user is None or not verify_secret(user.pin_hash, payload.pin):
        raise Forbidden("Owner or manager approval PIN is incorrect")
    token = generate_token()
    now = utc_now()
    approval = DiscountApproval(
        organization_id=context.organization_id,
        store_id=context.store_id,
        token_hash=hash_token(token),
        fingerprint=discount_fingerprint(payload),
        approved_by_user_id=user.id,
        expires_at=now + timedelta(minutes=5),
        created_at=now,
    )
    session.add(approval)
    await session.commit()
    return token, approval, user


async def _authorize_discount(session: AsyncSession, context: RequestContext, payload: Any, total_discount: Decimal) -> None:
    if total_discount <= 0:
        return
    organization = await session.get(Organization, context.organization_id)
    settings = coerce_settings(OrganizationSettings, organization.settings if organization else None)
    if not settings.require_pin_for_discounts:
        return
    if not payload.discount_approval_token:
        raise Forbidden("A manager PIN approval is required for this discount")
    approval = await session.scalar(
        select(DiscountApproval).where(
            DiscountApproval.organization_id == context.organization_id,
            DiscountApproval.store_id == context.store_id,
            DiscountApproval.token_hash == hash_token(payload.discount_approval_token),
        )
    )
    if (
        approval is None
        or approval.consumed_at is not None
        or (as_utc(approval.expires_at) or utc_now()) <= utc_now()
        or approval.fingerprint != discount_fingerprint(payload)
    ):
        raise Forbidden("Discount approval is invalid, expired, or belongs to a changed cart")
    approval.consumed_at = utc_now()


def allocate_discount(line_totals: list[Decimal], discount: Decimal) -> list[Decimal]:
    """Split a sale-level discount across lines in proportion to their line totals.

    Largest remainder on integer cents, so the parts sum to exactly ``discount``
    and no line absorbs a rounding ghost. This mirrors ``allocateLineDiscounts``
    in ``@pharmacy/sales``, which the clients already use to print the same split
    on a receipt.

    The discount only ever lives on ``sales.discount`` -- ``sale_items`` has no
    discount column -- so anything that needs to know what a *line* was actually
    worth has to re-derive it here. A refund is the case that matters: paying back
    the undiscounted ``unit_price`` hands over money the till never took.
    """
    cents = [int(Decimal(value).quantize(CENT) * 100) for value in line_totals]
    target = int(Decimal(discount).quantize(CENT) * 100)
    subtotal = sum(cents)
    if target <= 0 or subtotal <= 0:
        return [Decimal(0) for _ in line_totals]
    shares = [value * target // subtotal for value in cents]
    # Largest fractional part first, then earliest line, so one sale always splits
    # the same way however often it is recomputed.
    order = sorted(
        range(len(cents)),
        key=lambda index: (-((cents[index] * target) % subtotal), index),
    )
    for index in order[: target - sum(shares)]:
        shares[index] += 1
    return [(Decimal(share) / 100).quantize(CENT) for share in shares]



async def create_sale(
    session: AsyncSession,
    context: RequestContext,
    payload,
    *,
    idempotency_key: str,
    request_id: str,
) -> SaleCreated:
    """Create a completed POS sale in one atomic transaction.

    Totals are recomputed from ``store_products.sale_price``, batches are
    allocated FEFO, stock is consumed, tenders are recorded (due bumps the
    customer balance), and audit + outbox rows join the same commit.
    """
    if context.role not in STAFF_ROLES:
        raise Forbidden("Only staff may create sales")
    if context.store_id is None:
        raise NotFound("Sale not found")

    payload_dict = payload.model_dump(by_alias=True)
    stored = await replay(session, context.organization_id, idempotency_key, payload_dict)
    if stored is not None:
        return SaleCreated(sale=Sale(id=UUID(stored["id"])), replay_body=stored)

    customer = None
    if payload.customer_id is not None:
        customer = await load_customer(session, context, payload.customer_id)

    products_by_line: list[StoreProduct] = []
    for line in payload.items:
        products_by_line.append(
            await load_store_product(session, context, line.store_product_id)
        )

    totals = structured_totals(payload, products_by_line)
    discount = totals.line_discount + totals.global_discount
    await _authorize_discount(session, context, payload, discount)
    advance = (
        Decimal(payload.advance_application.amount).quantize(CENT)
        if payload.advance_application is not None
        else Decimal("0.00")
    )
    if advance > 0 and customer is None:
        raise ValidationError("Advance applications require a customer")
    if advance > totals.total:
        raise ValidationError("Advance cannot exceed the sale total")
    amount_due_now = (totals.total - advance).quantize(CENT)

    # Loyalty redemption is priced and guarded here, inside the sale's
    # transaction, because only the server can convert points to money at the
    # configured rate and only a transaction can keep two tills from spending
    # one balance twice. The ledger row is written after the payments clear.
    loyalty_points = 0
    loyalty_credit = Decimal("0.00")
    loyalty_account = None
    loyalty_balance_after: int | None = None
    if payload.loyalty_redemption is not None:
        if customer is None:
            raise ValidationError("Loyalty redemption requires a customer")
        loyalty_account = await get_account_by_customer(session, context, payload.customer_id)
        loyalty_points = payload.loyalty_redemption.points
        if int(loyalty_account.balance) < loyalty_points:
            raise ValidationError("Insufficient loyalty points")
        organization = await session.get(Organization, context.organization_id)
        settings = coerce_settings(
            OrganizationSettings, organization.settings if organization else None
        )
        point_value = Decimal(settings.loyalty_point_value)
        if point_value <= 0:
            raise ValidationError("Loyalty redemption is not enabled for this organization")
        loyalty_credit = (Decimal(loyalty_points) * point_value).quantize(CENT)
        if loyalty_credit > amount_due_now:
            raise ValidationError("Loyalty credit exceeds the amount due")
        amount_due_now = (amount_due_now - loyalty_credit).quantize(CENT)

    # Allocate every line first so one shortfall aborts before any write.
    allocations_by_line: list[list[Any]] = []
    for line in payload.items:
        result = await allocate_fefo_for_product(session, context, line.store_product_id, line.quantity)
        if not result.ok:
            raise InsufficientStock(
                f"Insufficient stock for store product '{line.store_product_id}'"
            )
        allocations_by_line.append(result.allocations)

    now = utc_now()
    sale = Sale(
        organization_id=context.organization_id,
        store_id=context.store_id,
        customer_id=payload.customer_id,
        channel=SaleChannel.POS,
        status=SaleStatus.COMPLETED,
        subtotal=totals.subtotal,
        discount=discount,
        line_discount=totals.line_discount,
        global_discount=totals.global_discount,
        delivery_charge=totals.delivery_charge,
        other_fee_label=totals.other_fee_label,
        other_fee=totals.other_fee,
        advance_applied=advance,
        advance_reference=(payload.advance_application.reference if payload.advance_application else None),
        loyalty_points_redeemed=loyalty_points,
        loyalty_credit=loyalty_credit,
        total=totals.total,
        idempotency_key=idempotency_key,
        receipt_number=await _next_receipt_number(session, context.store_id),
        created_at=now,
    )
    session.add(sale)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise Conflict("Sale could not be created") from exc

    items: list[SaleItem] = []
    for line, product, allocations, line_discount in zip(
        payload.items, products_by_line, allocations_by_line, totals.line_discounts
    ):
        gross_line_total = (Decimal(line.quantity) * Decimal(product.sale_price)).quantize(CENT)
        item = SaleItem(
            sale_id=sale.id,
            store_product_id=line.store_product_id,
            product_name=product.sku,
            quantity=line.quantity,
            unit_price=Decimal(product.sale_price).quantize(CENT),
            discount_mode=line.discount.mode if line.discount is not None else None,
            discount_value=Decimal(line.discount.value) if line.discount is not None else Decimal(0),
            discount_amount=line_discount,
            line_total=gross_line_total,
        )
        session.add(item)
        await session.flush()
        items.append(item)
        for allocation in allocations:
            session.add(
                SaleItemBatchAllocation(
                    sale_item_id=item.id,
                    batch_id=allocation.batch_id,
                    quantity=allocation.quantity,
                )
            )
        try:
            await _consume(session, context, line.store_product_id, allocations, sale)
        except InsufficientStock as exc:
            await session.rollback()
            raise Conflict("Insufficient stock to complete the sale") from exc

    payments: list[Any] = []
    paid = Decimal(0)
    for index, tender in enumerate(payload.payments):
        payment, _change = await create_sale_payment(
            session,
            context,
            sale=sale,
            method=tender.method,
            amount=tender.amount,
            received_amount=tender.received_amount,
            provider_reference=tender.provider_reference,
            idempotency_key=f"{idempotency_key}:{index}",
            request_id=request_id,
        )
        payments.append(payment)
        paid += Decimal(tender.amount)
    if paid.quantize(CENT) != amount_due_now:
        await session.rollback()
        raise Conflict("Payments must add up to the amount due after advance and loyalty credit")

    if loyalty_account is not None and loyalty_points > 0:
        loyalty_balance_after = redeem_for_sale(
            session,
            context,
            loyalty_account,
            points=loyalty_points,
            sale_id=sale.id,
            idempotency_key=idempotency_key,
            request_id=request_id,
        )

    record_audit(
        session,
        context,
        action="sale.created",
        entity_type="sale",
        entity_id=sale.id,
        request_id=request_id,
        after=redact({"total": str(totals.total), "advance": str(advance), "customer_id": str(payload.customer_id)}),
    )
    enqueue_outbox(
        session,
        organization_id=context.organization_id,
        event_type="sale.created",
        aggregate_type="sale",
        aggregate_id=sale.id,
        payload={
            "sale_id": str(sale.id),
            "store_id": str(sale.store_id),
            "total": str(totals.total),
            "receipt_number": sale.receipt_number,
        },
    )

    body = sale_body(sale, items, payments)
    if loyalty_balance_after is not None:
        # Only the creating call knows the balance after redemption; a re-read
        # of the sale cannot reconstruct it without replaying every later
        # transaction, so the field stays null everywhere else.
        body["loyaltyBalanceAfter"] = loyalty_balance_after
    remember(
        session,
        context.organization_id,
        idempotency_key,
        payload_dict,
        response_status=201,
        response_body=body,
    )
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise Conflict("Sale already exists for this idempotency key") from exc
    except Exception:
        await session.rollback()
        raise
    return SaleCreated(sale=sale, items=items, payments=payments, loyalty_balance_after=loyalty_balance_after)


async def _consume(
    session: AsyncSession,
    context: RequestContext,
    store_product_id: UUID,
    allocations: list[Any],
    sale: Sale,
) -> None:
    """Write negative SALE movements per batch and decrement the projection."""
    from app.services.inventory import consume_allocations

    await consume_allocations(
        session,
        context,
        store_product_id,
        allocations,
        InventoryMovementType.SALE,
        reference_type="sale",
        reference_id=sale.id,
        commit=False,
    )


async def _sold_batches(
    session: AsyncSession, sale_id: UUID
) -> dict[UUID, dict[UUID, Decimal]]:
    """Units taken per store product and batch, read off the sale's own allocations.

    Ordered by allocation id, which is the order FEFO picked the batches at the
    time of sale, so a partial return puts stock back the way it came out.
    """
    rows = await session.execute(
        select(
            SaleItem.store_product_id,
            SaleItemBatchAllocation.batch_id,
            SaleItemBatchAllocation.quantity,
        )
        .join(SaleItem, SaleItem.id == SaleItemBatchAllocation.sale_item_id)
        .where(SaleItem.sale_id == sale_id)
        .order_by(SaleItemBatchAllocation.id)
    )
    sold: dict[UUID, dict[UUID, Decimal]] = {}
    for store_product_id, batch_id, quantity in rows:
        per_batch = sold.setdefault(store_product_id, {})
        per_batch[batch_id] = per_batch.get(batch_id, Decimal(0)) + Decimal(quantity)
    return sold


def _restored_batches(
    movements: list[InventoryMovement],
) -> dict[UUID, dict[UUID, Decimal]]:
    """What prior returns already put back, per store product and batch."""
    restored: dict[UUID, dict[UUID, Decimal]] = {}
    for movement in movements:
        if movement.batch_id is None:
            continue
        per_batch = restored.setdefault(movement.store_product_id, {})
        per_batch[movement.batch_id] = per_batch.get(
            movement.batch_id, Decimal(0)
        ) + abs(Decimal(movement.quantity))
    return restored


def _plan_restore(
    requested: dict[UUID, Decimal],
    sold_batches: dict[UUID, dict[UUID, Decimal]],
    restored_batches: dict[UUID, dict[UUID, Decimal]],
) -> dict[UUID, list[tuple[UUID, Decimal]]]:
    """Assign each returned unit back to the batch the sale took it from.

    Capacity per batch is what the sale took less what earlier returns already put
    back, so the plan can always be satisfied: the caller has already capped the
    request at the un-returned quantity, and that cap is the sum of these
    capacities. The ``InsufficientStock`` below is therefore an invariant check,
    not a business rule -- a return is a credit against movements that exist.
    """
    plan: dict[UUID, list[tuple[UUID, Decimal]]] = {}
    for store_product_id, quantity in requested.items():
        remaining = Decimal(quantity)
        already = restored_batches.get(store_product_id, {})
        assignments: list[tuple[UUID, Decimal]] = []
        for batch_id, sold_quantity in sold_batches.get(store_product_id, {}).items():
            if remaining <= 0:
                break
            capacity = sold_quantity - already.get(batch_id, Decimal(0))
            if capacity <= 0:
                continue
            take = min(remaining, capacity)
            assignments.append((batch_id, take))
            remaining -= take
        if remaining > 0:
            raise InsufficientStock(
                f"Sale allocations do not account for the returned quantity of "
                f"store product '{store_product_id}'"
            )
        plan[store_product_id] = assignments
    return plan


async def _restore_stock(
    session: AsyncSession,
    context: RequestContext,
    plan: dict[UUID, list[tuple[UUID, Decimal]]],
    *,
    reference_type: str,
    reference_id: UUID,
) -> None:
    """Put returned stock back into the batches it was sold from.

    The batch comes from the sale's own ``sale_item_batch_allocations`` rows rather
    than being chosen afresh. Re-running the FEFO *allocator* here was wrong twice
    over. It only considers batches with stock still on the shelf and an expiry in
    the future, so a sale that emptied the shelf -- or a batch that expired between
    the sale and the return -- refused the refund outright, with no way to pay the
    customer back at all. And when it did succeed the units could land in a
    different batch than they left, which silently mis-stated gross profit, because
    reports value returned stock at the receiving batch's ``unit_cost``.

    A return needs no availability check: the units exist because the sale took
    them. Restoring into a batch that has since expired is correct -- that is where
    the stock physically is -- and expired batches stay out of future allocations,
    so nothing resells them.
    """
    now = utc_now()
    for store_product_id, assignments in plan.items():
        store_product = await session.get(StoreProduct, store_product_id)
        assert store_product is not None
        restored = Decimal(0)
        for batch_id, quantity in assignments:
            session.add(
                InventoryMovement(
                    organization_id=store_product.organization_id,
                    store_id=store_product.store_id,
                    store_product_id=store_product_id,
                    batch_id=batch_id,
                    movement_type=InventoryMovementType.RETURN,
                    quantity=quantity,
                    reference_type=reference_type,
                    reference_id=reference_id,
                    idempotency_key=str(uuid4()),
                    occurred_at=now,
                    actor_user_id=context.user_id,
                )
            )
            restored += quantity
        await _apply_to_balance(session, store_product, restored)



def _sold_quantities(items: list[SaleItem]) -> dict[UUID, Decimal]:
    """Units sold per store product, summed across every line of the sale.

    A cart can carry the same product on more than one line -- scan it twice and
    ``create_sale`` writes two ``sale_items`` rows, because nothing merges them. So
    "how much of this product did the sale contain" is a sum, never one line's
    quantity, and both the return cap and the void restore depend on that sum.
    """
    sold: dict[UUID, Decimal] = {}
    for item in items:
        sold[item.store_product_id] = (
            sold.get(item.store_product_id, Decimal(0)) + Decimal(item.quantity)
        )
    return sold


def _restored_quantities(
    movements: list[InventoryMovement],
) -> dict[UUID, Decimal]:
    restored: dict[UUID, Decimal] = {}
    for movement in movements:
        if movement.batch_id is None:
            continue
        restored[movement.store_product_id] = (
            restored.get(movement.store_product_id, Decimal(0)) + abs(Decimal(movement.quantity))
        )
    return restored


def _net_by_product(items: list[SaleItem], global_discount: Decimal) -> dict[UUID, Decimal]:
    """What each store product was actually worth after the sale-level discount.

    ``sales.discount`` is a single figure against the whole cart, so a line's own
    share of it has to be re-derived; :func:`allocate_discount` does that the same
    way the clients do when they print the receipt.
    """
    lines = [Decimal(item.line_total) - Decimal(item.discount_amount) for item in items]
    net: dict[UUID, Decimal] = {}
    for item, line_net, share in zip(items, lines, allocate_discount(lines, global_discount)):
        net[item.store_product_id] = (
            net.get(item.store_product_id, Decimal(0)) + line_net - share
        )
    return net


def _returned_value(net_total: Decimal, sold: Decimal, quantity: Decimal) -> Decimal:
    """Value of ``quantity`` units when ``sold`` units were worth ``net_total``."""
    if sold <= 0:
        return Decimal(0)
    return (net_total * quantity / sold).quantize(CENT)


def _refund_amount(
    net: dict[UUID, Decimal],
    sold: dict[UUID, Decimal],
    returned: dict[UUID, Decimal],
    requested: dict[UUID, Decimal],
) -> Decimal:
    """Refund for this return: the discounted value of the units going back.

    Priced off the *net* line value rather than ``sale_items.unit_price``. The
    unit price is what the item was rung up at before the cart-level discount, so
    refunding it handed back money the till never took -- a 100.00 cart sold for
    80.00 refunded the full 100.00, and the shop paid 20.00 for the privilege of
    accepting a return.

    Each product's figure is the difference between the value of everything
    returned so far and the value of everything returned before this one, so
    successive partial returns of a product whose net value does not divide
    evenly still add up to exactly that value -- no cent is paid twice or lost in
    the last return.
    """
    total = Decimal(0)
    for product_id, quantity in requested.items():
        net_total = net.get(product_id, Decimal(0))
        sold_quantity = sold.get(product_id, Decimal(0))
        before = returned.get(product_id, Decimal(0))
        total += _returned_value(net_total, sold_quantity, before + quantity) - _returned_value(
            net_total, sold_quantity, before
        )
    return total.quantize(CENT)


def sale_return_body(sale_return: SaleReturn) -> dict[str, Any]:
    """Camel-case wire shape, used for responses and idempotency replays alike."""
    import json

    from app.schemas.sales import SaleReturnResponse

    body = SaleReturnResponse(
        id=sale_return.id,
        sale_id=sale_return.sale_id,
        reason=sale_return.reason,
        total=Decimal(sale_return.total),
        advance_restored=Decimal(sale_return.advance_restored),
        created_at=sale_return.created_at,
    )
    return json.loads(body.model_dump_json(by_alias=True))


async def create_sale_return(
    session: AsyncSession,
    context: RequestContext,
    sale_id: UUID,
    payload,
    *,
    idempotency_key: str,
    request_id: str,
) -> SaleReturnCreated:
    """Return lines of a completed sale back into stock within sold limits.

    The cap is enforced per *store product*, not per sale line, because that is the
    granularity the ledger can actually enforce: a return leaves behind inventory
    movements keyed by product, so prior returns are only recoverable at that
    granularity. It is also the correct limit -- the same product may sit on two
    lines of one cart, and the two lines carry the same unit price (both are read
    from ``store_products.sale_price`` in the same transaction), so a per-product
    total gives the identical refund while a per-line cap wrongly refuses to take
    back everything the customer bought.

    ``sale_item_id`` still names the line: it proves the line belongs to this sale
    and identifies the product being handed back.

    Idempotent on ``idempotency_key``, like ``create_sale``. Without it a
    double-tapped Refund button -- or the same offline event replayed by the sync
    feed -- paid the customer twice and put the stock back twice, and the second
    call looked like a legitimate second return because nothing tied it to the
    first.
    """
    if context.role not in STAFF_ROLES:
        raise Forbidden("Only staff may create returns")

    payload_dict = payload.model_dump(by_alias=True)
    stored = await replay(session, context.organization_id, idempotency_key, payload_dict)
    if stored is not None:
        return SaleReturnCreated(sale_return=None, replay_body=stored)

    sale = await load_sale(session, context, sale_id)
    if sale.status is not SaleStatus.COMPLETED:
        raise Conflict("Only completed sales can be returned")

    item_rows = await _load_items(session, sale.id)
    items = {item.id: item for item in item_rows}
    sold = _sold_quantities(item_rows)
    prior_movements = list(
        await session.scalars(
            select(InventoryMovement).where(
                InventoryMovement.reference_type == "sale_return",
                InventoryMovement.reference_id == sale.id,
            )
        )
    )
    returned = _restored_quantities(prior_movements)

    requested: dict[UUID, Decimal] = {}
    for line in payload.lines:
        item = items.get(line.sale_item_id)
        if item is None:
            raise NotFound("Sale item not found")
        product_id = item.store_product_id
        already = returned.get(product_id, Decimal(0)) + requested.get(product_id, Decimal(0))
        if sold[product_id] - already < Decimal(line.quantity):
            raise Conflict("Return quantity exceeds remaining returnable quantity")
        requested[product_id] = requested.get(product_id, Decimal(0)) + Decimal(line.quantity)

    refund_total = _refund_amount(
        _net_by_product(item_rows, Decimal(sale.global_discount)), sold, returned, requested
    )

    try:
        plan = _plan_restore(
            requested, await _sold_batches(session, sale.id), _restored_batches(prior_movements)
        )
        await _restore_stock(
            session,
            context,
            plan,
            reference_type="sale_return",
            reference_id=sale.id,
        )
    except InsufficientStock as exc:
        await session.rollback()
        raise Conflict("Insufficient stock capacity to process the return") from exc

    now = utc_now()
    prior_advance_restored = sum(
        await session.scalars(select(SaleReturn.advance_restored).where(SaleReturn.sale_id == sale.id)),
        Decimal(0),
    )
    advance_restored = min(
        refund_total,
        max(Decimal(sale.advance_applied) - Decimal(prior_advance_restored), Decimal(0)),
    ).quantize(CENT)
    sale_return = SaleReturn(
        organization_id=sale.organization_id,
        store_id=sale.store_id,
        sale_id=sale.id,
        reason=payload.reason.strip(),
        total=(-refund_total).quantize(CENT),
        advance_restored=advance_restored,
        idempotency_key=idempotency_key,
        created_at=now,
    )
    session.add(sale_return)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise Conflict("Return could not be created") from exc

    # The refund is booked against the layers that paid the sale, in reverse:
    # advance money returns to the customer's advance balance first, then the
    # loyalty-credit share comes back as points, and only what the tenders
    # actually carried is refunded as money. Refunding a redemption's worth of
    # cash against a 5.00 tender is how a sale's own books come to disagree with
    # the ledger -- ``refund_payments`` refuses it, and rightly.
    if advance_restored > 0:
        assert sale.customer_id is not None
        customer = await load_customer(session, context, sale.customer_id)
        customer.advance_balance = Decimal(customer.advance_balance) + advance_restored
    refund_after_advance = refund_total - advance_restored
    credit = Decimal(sale.loyalty_credit)
    tender_capacity = Decimal(sale.total) - Decimal(sale.advance_applied) - credit
    loyalty_share = Decimal("0.00")
    if credit > 0 and tender_capacity > 0:
        loyalty_share = (refund_after_advance * credit / (credit + tender_capacity)).quantize(CENT)
    elif credit > 0:
        loyalty_share = refund_after_advance
    tender_refund = (refund_after_advance - loyalty_share).quantize(CENT)
    if tender_refund > 0:
        await refund_payments(
            session,
            context,
            await _load_payments(session, sale.id),
            tender_refund,
            reason=sale_return.reason,
            request_id=request_id,
            key_prefix=f"return:{sale_return.id}",
        )

    # Points proportional to the refunded credit share. The floor can leave a
    # fraction of a point unreturned on the last partial return; rounding up
    # would hand back points the customer never paid for, which is the worse
    # drift.
    if loyalty_share > 0 and sale.customer_id is not None and credit > 0:
        account = await find_account_for_customer(session, context.organization_id, sale.customer_id)
        if account is not None:
            restore = int(loyalty_share * int(sale.loyalty_points_redeemed) / credit)
            if restore > 0:
                restore_for_sale(
                    session,
                    context,
                    account,
                    points=restore,
                    sale_id=sale.id,
                    idempotency_key=f"return:{sale_return.id}",
                    request_id=request_id,
                )

    record_audit(
        session,
        context,
        action="sale.returned",
        entity_type="sale",
        entity_id=sale.id,
        request_id=request_id,
        after={"return_id": str(sale_return.id), "refund_total": str(refund_total)},
    )
    # A return moves revenue and stock exactly as ``sale.created`` and
    # ``sale.voided`` do, so it has to reach the same consumers. Without this the
    # only correction that never left the database was the commonest one, and a
    # branch's figures drifted by every refund it took.
    enqueue_outbox(
        session,
        organization_id=context.organization_id,
        event_type="sale.returned",
        aggregate_type="sale",
        aggregate_id=sale.id,
        payload={
            "sale_id": str(sale.id),
            "store_id": str(sale.store_id),
            "return_id": str(sale_return.id),
            "total": str(sale_return.total),
            "reason": sale_return.reason,
        },
    )

    body = sale_return_body(sale_return)
    remember(
        session,
        context.organization_id,
        idempotency_key,
        payload_dict,
        response_status=201,
        response_body=body,
    )
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise Conflict("Return could not be created") from exc
    return SaleReturnCreated(sale_return=sale_return, replay_body=None)


async def void_sale(
    session: AsyncSession,
    context: RequestContext,
    sale_id: UUID,
    payload,
    *,
    request_id: str,
) -> Sale:
    """Void a same-day sale; owner/manager only, restores all stock.

    A sale that already has a return against it cannot be voided. The two
    corrections are not composable, because each removes the money a different
    way: a return subtracts a refund line from a sale that stays in revenue,
    while a void drops the sale out of revenue altogether. Applying both leaves
    the day reporting a refund against sales that no longer exist -- net revenue
    goes negative by the returned amount -- and restores the returned units to
    the shelf twice, so ``on_hand`` ends higher than the stock ever was. The
    remedy is to return the remaining lines instead, which settles the same
    money and the same stock while keeping both ledgers honest.
    """
    if context.role not in VOID_ROLES:
        raise Forbidden("Only owners and managers may void sales")
    sale = await load_sale(session, context, sale_id)
    if sale.status is not SaleStatus.COMPLETED:
        raise Conflict("Only completed sales can be voided")
    # "Same day" is the branch's trading day, not the server's UTC date. Comparing UTC
    # dates broke both ways for a shop east of Greenwich: a sale rung up at 01:30 local
    # carries yesterday's UTC date, so it became unvoidable hours into the very shift
    # that made it -- while a sale from late last night shared today's UTC date and
    # stayed voidable after cash-up, letting an already reconciled day's revenue change
    # behind the till. ``business_date`` is what reports cut on too, so the sales a
    # report counts as today's are now exactly the ones that can still be voided.
    #
    # ``as_utc`` is not decoration: SQLite hands the timestamp back naive, and
    # ``local_now`` would then read it as server-local rather than UTC -- six hours
    # off for this deployment, which is enough to land the sale on the wrong day.
    store = await load_store(session, context, sale.store_id)
    if business_date(store, moment=as_utc(sale.created_at)) != business_date(store):
        raise Conflict("Only same-day sales can be voided")
    prior_return = await session.scalar(
        select(func.count()).select_from(SaleReturn).where(SaleReturn.sale_id == sale.id)
    )
    if prior_return:
        raise Conflict("A sale with a return against it cannot be voided; return the remaining lines instead")

    items = await _load_items(session, sale.id)
    quantities = _sold_quantities(items)
    try:
        # Nothing has been restored yet: a sale with a return against it cannot be
        # voided, which the check above has already enforced.
        plan = _plan_restore(quantities, await _sold_batches(session, sale.id), {})
        await _restore_stock(
            session, context, plan, reference_type="void", reference_id=sale.id
        )
    except InsufficientStock as exc:
        await session.rollback()
        raise Conflict("Insufficient stock capacity to void the sale") from exc

    sale.status = SaleStatus.VOIDED
    sale.void_reason = payload.reason.strip()

    # A voided sale leaves every revenue report, so any credit it created has to
    # go with it -- otherwise the customer owes for a transaction the books deny.
    # The tenders only ever carried what the advance and the loyalty credit did
    # not, so that is all ``refund_payments`` may hand back.
    tender_total = (
        Decimal(sale.total)
        - Decimal(sale.advance_applied)
        - Decimal(sale.loyalty_credit)
    )
    if tender_total > 0:
        await refund_payments(
            session,
            context,
            await _load_payments(session, sale.id),
            tender_total,
            reason=f"void: {sale.void_reason}",
            request_id=request_id,
            key_prefix=f"void:{sale.id}",
        )
    if Decimal(sale.advance_applied) > 0 and sale.customer_id is not None:
        customer = await load_customer(session, context, sale.customer_id)
        customer.advance_balance = Decimal(customer.advance_balance) + Decimal(sale.advance_applied)

    # A void drops the sale out of revenue entirely, so the points it burned
    # come back in full -- unlike a partial return, there is nothing left for
    # the remainder of the redemption to be "for".
    if sale.loyalty_points_redeemed > 0 and sale.customer_id is not None:
        account = await find_account_for_customer(session, context.organization_id, sale.customer_id)
        if account is not None:
            restore_for_sale(
                session,
                context,
                account,
                points=int(sale.loyalty_points_redeemed),
                sale_id=sale.id,
                idempotency_key=f"void:{sale.id}",
                request_id=request_id,
            )

    record_audit(
        session,
        context,
        action="sale.voided",
        entity_type="sale",
        entity_id=sale.id,
        request_id=request_id,
        after={"reason": sale.void_reason, "total": str(sale.total)},
    )
    enqueue_outbox(
        session,
        organization_id=context.organization_id,
        event_type="sale.voided",
        aggregate_type="sale",
        aggregate_id=sale.id,
        # ``store_id`` travels with the event, as it does on ``sale.created``. The row
        # carries only the organization, so without it a consumer cannot tell which
        # branch reversed a sale -- and a void that cannot be routed to a branch cannot
        # correct the branch figure the matching ``sale.created`` already moved.
        payload={
            "sale_id": str(sale.id),
            "store_id": str(sale.store_id),
            "total": str(sale.total),
            "reason": sale.void_reason,
        },
    )
    await session.commit()
    return sale


def sale_body(sale: Sale, items: list[SaleItem], payments: list[Any]) -> dict[str, Any]:
    """Camel-case wire shape used both for responses and idempotency replays."""
    from app.schemas.payments import PaymentResponse
    from app.schemas.sales import SaleItemResponse, SaleResponse

    body = SaleResponse(
        id=sale.id,
        organization_id=sale.organization_id,
        store_id=sale.store_id,
        customer_id=sale.customer_id,
        channel=sale.channel,
        status=sale.status,
        subtotal=Decimal(sale.subtotal),
        discount=Decimal(sale.discount),
        line_discount=Decimal(sale.line_discount),
        global_discount=Decimal(sale.global_discount),
        delivery_charge=Decimal(sale.delivery_charge),
        other_fee_label=sale.other_fee_label,
        other_fee=Decimal(sale.other_fee),
        advance_applied=Decimal(sale.advance_applied),
        advance_reference=sale.advance_reference,
        loyalty_points_redeemed=int(sale.loyalty_points_redeemed),
        loyalty_credit=Decimal(sale.loyalty_credit),
        loyalty_balance_after=None,
        amount_due_now=(
            Decimal(sale.total)
            - Decimal(sale.advance_applied)
            - Decimal(sale.loyalty_credit)
        ).quantize(CENT),
        total=Decimal(sale.total),
        receipt_number=sale.receipt_number,
        void_reason=sale.void_reason,
        created_at=sale.created_at,
        items=[
            SaleItemResponse(
                id=item.id,
                store_product_id=item.store_product_id,
                product_name=item.product_name,
                quantity=Decimal(item.quantity),
                unit_price=Decimal(item.unit_price),
                discount_mode=item.discount_mode,
                discount_value=Decimal(item.discount_value),
                discount_amount=Decimal(item.discount_amount),
                line_total=Decimal(item.line_total),
            )
            for item in items
        ],
        payments=[PaymentResponse.model_validate(payment) for payment in payments],
    )
    import json

    return json.loads(body.model_dump_json(by_alias=True))


async def get_sale_detail(
    session: AsyncSession, context: RequestContext, sale_id: UUID
) -> tuple[Sale, list[SaleItem], list[Any]]:
    sale = await load_sale(session, context, sale_id)
    return sale, await _load_items(session, sale.id), await _load_payments(session, sale.id)


async def list_sales(
    session: AsyncSession,
    context: RequestContext,
    *,
    customer_id: UUID | None = None,
    status: SaleStatus | None = None,
    limit: int = 25,
    offset: int = 0,
) -> tuple[list[Sale], int]:
    scope: list = [Sale.organization_id == context.organization_id]
    if context.store_id is not None:
        scope.append(Sale.store_id == context.store_id)
    if customer_id is not None:
        scope.append(Sale.customer_id == customer_id)
    if status is not None:
        scope.append(Sale.status == status)
    total = await session.scalar(select(func.count()).select_from(Sale).where(*scope))
    rows = list(
        await session.scalars(
            select(Sale)
            .where(*scope)
            .order_by(Sale.created_at.desc(), Sale.id)
            .limit(limit)
            .offset(offset)
        )
    )
    return rows, int(total or 0)

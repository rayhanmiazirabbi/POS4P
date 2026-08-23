from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.context import RequestContext
from app.domains.inventory import (
    InventoryBatch,
    InventoryMovement,
    InventoryMovementType,
)
from app.domains.sales import (
    Sale,
    SaleChannel,
    SaleItem,
    SaleItemBatchAllocation,
    SaleReturn,
    SaleStatus,
)
from app.domains.sync import StoreSequence
from app.errors import Conflict, Forbidden, NotFound, ValidationError
from app.models import Role, StoreProduct
from app.security import as_utc, utc_now
from app.services.audit import enqueue_outbox, record_audit, redact
from app.services.customers import load_customer
from app.services.idempotency import remember, replay
from app.services.inventory import (
    InsufficientStock,
    _apply_to_balance,
    allocate_fefo_for_product,
    load_store_product,
)
from app.services.payments import create_sale_payment, refund_payments
from app.services.stores import business_date, load_store

STAFF_ROLES = frozenset({Role.OWNER, Role.MANAGER, Role.CASHIER})
VOID_ROLES = frozenset({Role.OWNER, Role.MANAGER})

CENT = Decimal("0.01")


@dataclass
class SaleCreated:
    sale: Sale
    items: list[SaleItem] = field(default_factory=list)
    payments: list[Any] = field(default_factory=list)
    #: Set when an idempotent replay served a stored response body instead.
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

    if payload.customer_id is not None:
        await load_customer(session, context, payload.customer_id)

    products_by_line: list[StoreProduct] = []
    for line in payload.items:
        products_by_line.append(
            await load_store_product(session, context, line.store_product_id)
        )

    subtotal, discount, total = compute_totals(
        [(line.quantity, Decimal(product.sale_price)) for line, product in zip(payload.items, products_by_line)],
        payload.discount,
    )

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
        subtotal=subtotal,
        discount=discount,
        total=total,
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
    for line, product, allocations in zip(payload.items, products_by_line, allocations_by_line):
        item = SaleItem(
            sale_id=sale.id,
            store_product_id=line.store_product_id,
            product_name=product.sku,
            quantity=line.quantity,
            unit_price=Decimal(product.sale_price).quantize(CENT),
            line_total=(Decimal(line.quantity) * Decimal(product.sale_price)).quantize(CENT),
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
    if paid.quantize(CENT) != total:
        await session.rollback()
        raise Conflict("Payments must add up to the sale total")

    record_audit(
        session,
        context,
        action="sale.created",
        entity_type="sale",
        entity_id=sale.id,
        request_id=request_id,
        after=redact({"total": str(total), "customer_id": str(payload.customer_id)}),
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
            "total": str(total),
            "receipt_number": sale.receipt_number,
        },
    )

    body = sale_body(sale, items, payments)
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
    return SaleCreated(sale=sale, items=items, payments=payments)


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


async def _restore_stock(
    session: AsyncSession,
    context: RequestContext,
    quantities: dict[UUID, Decimal],
    *,
    reference_type: str,
    reference_id: UUID,
) -> None:
    """Return stock to shelves FEFO via positive RETURN movements."""
    for store_product_id, quantity in quantities.items():
        result = await allocate_fefo_for_product(session, context, store_product_id, quantity)
        if not result.ok:
            raise InsufficientStock(f"Insufficient stock to process the return")
        store_product = await session.get(StoreProduct, store_product_id)
        assert store_product is not None
        now = utc_now()
        for allocation in result.allocations:
            batch = await session.get(InventoryBatch, allocation.batch_id)
            session.add(
                InventoryMovement(
                    organization_id=store_product.organization_id,
                    store_id=store_product.store_id,
                    store_product_id=store_product_id,
                    batch_id=allocation.batch_id,
                    movement_type=InventoryMovementType.RETURN,
                    quantity=abs(allocation.quantity),
                    reference_type=reference_type,
                    reference_id=reference_id,
                    idempotency_key=str(uuid4()),
                    occurred_at=now,
                    actor_user_id=context.user_id,
                )
            )
            assert batch is not None
        await _apply_to_balance(session, store_product, quantity)


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


async def create_sale_return(
    session: AsyncSession,
    context: RequestContext,
    sale_id: UUID,
    payload,
    *,
    request_id: str,
) -> SaleReturn:
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
    and supplies the unit price the refund is computed at.
    """
    if context.role not in STAFF_ROLES:
        raise Forbidden("Only staff may create returns")
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
    refund_total = Decimal(0)
    for line in payload.lines:
        item = items.get(line.sale_item_id)
        if item is None:
            raise NotFound("Sale item not found")
        product_id = item.store_product_id
        already = returned.get(product_id, Decimal(0)) + requested.get(product_id, Decimal(0))
        if sold[product_id] - already < Decimal(line.quantity):
            raise Conflict("Return quantity exceeds remaining returnable quantity")
        requested[product_id] = requested.get(product_id, Decimal(0)) + Decimal(line.quantity)
        refund_total += Decimal(line.quantity) * Decimal(item.unit_price)
    refund_total = refund_total.quantize(CENT)

    try:
        await _restore_stock(
            session,
            context,
            requested,
            reference_type="sale_return",
            reference_id=sale.id,
        )
    except InsufficientStock as exc:
        await session.rollback()
        raise Conflict("Insufficient stock capacity to process the return") from exc

    now = utc_now()
    sale_return = SaleReturn(
        organization_id=sale.organization_id,
        store_id=sale.store_id,
        sale_id=sale.id,
        reason=payload.reason.strip(),
        total=(-refund_total).quantize(CENT),
        idempotency_key=f"return:{uuid4()}",
        created_at=now,
    )
    session.add(sale_return)
    await session.flush()

    # The refund is booked against the tenders it came in on, which is what keeps
    # the customer's due balance and the day's payment mix derivable from the ledger.
    await refund_payments(
        session,
        context,
        await _load_payments(session, sale.id),
        refund_total,
        reason=sale_return.reason,
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
    await session.commit()
    return sale_return


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
        await _restore_stock(
            session, context, quantities, reference_type="void", reference_id=sale.id
        )
    except InsufficientStock as exc:
        await session.rollback()
        raise Conflict("Insufficient stock capacity to void the sale") from exc

    sale.status = SaleStatus.VOIDED
    sale.void_reason = payload.reason.strip()

    # A voided sale leaves every revenue report, so any credit it created has to
    # go with it -- otherwise the customer owes for a transaction the books deny.
    await refund_payments(
        session,
        context,
        await _load_payments(session, sale.id),
        Decimal(sale.total),
        reason=f"void: {sale.void_reason}",
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

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.context import RequestContext
from app.domains.catalog import CatalogProduct
from app.domains.customers import Customer
from app.domains.ecommerce import EcommerceProductSetting
from app.domains.inventory import (
    Allocation,
    InventoryBalance,
    InventoryMovementType,
    StockReservation,
)
from app.domains.orders import (
    Order,
    OrderItem,
    OrderStatus,
    OrderStatusHistory,
    apply_order_transition,
)
from app.domains.products import PharmacyProduct, StoreProduct
from app.domains.prescriptions import Prescription, PrescriptionStatus
from app.domains.sales import Sale, SaleChannel, SaleItem, SaleItemBatchAllocation, SaleStatus
from app.errors import Conflict, Forbidden, NotFound, ValidationError
from app.models import Role
from app.security import utc_now
from app.services import inventory as inventory_service
from app.services.audit import enqueue_outbox, record_audit, redact
from app.services.idempotency import remember, replay

CENT = Decimal("0.01")

STAFF_ROLES = frozenset({Role.OWNER, Role.MANAGER, Role.CASHIER})

#: How long a checkout hold lives. A reservation that outlives its order's
#: payment window must not lock the shelf forever: expired holds stop counting
#: against availability and are swept back into the projection by
#: :func:`inventory_service.release_expired_reservations`, which every new
#: checkout runs first. Completing an order whose holds were swept conflicts,
#: so staff cancel it -- the state machine keeps that explicit.
RESERVATION_TTL = timedelta(hours=24)


async def load_order(session: AsyncSession, context: RequestContext, order_id: UUID) -> Order:
    """An order of another tenant (or branch) does not exist for the caller."""
    order = await session.get(Order, order_id)
    if order is None or order.organization_id != context.organization_id:
        raise NotFound("Order not found")
    if context.store_id is not None and order.store_id != context.store_id:
        raise NotFound("Order not found")
    return order


async def _load_items(session: AsyncSession, order_id: UUID) -> list[OrderItem]:
    return list(
        await session.scalars(
            select(OrderItem).where(OrderItem.order_id == order_id).order_by(OrderItem.id)
        )
    )


async def _load_history(session: AsyncSession, order_id: UUID) -> list[OrderStatusHistory]:
    return list(
        await session.scalars(
            select(OrderStatusHistory)
            .where(OrderStatusHistory.order_id == order_id)
            .order_by(OrderStatusHistory.created_at, OrderStatusHistory.id)
        )
    )


async def list_orders(
    session: AsyncSession,
    context: RequestContext,
    *,
    status: OrderStatus | None = None,
    customer_id: UUID | None = None,
) -> list[Order]:
    scope: list = [Order.organization_id == context.organization_id]
    if context.store_id is not None:
        scope.append(Order.store_id == context.store_id)
    if status is not None:
        scope.append(Order.status == status)
    if customer_id is not None:
        scope.append(Order.customer_id == customer_id)
    return list(
        await session.scalars(
            select(Order).where(*scope).order_by(Order.created_at.desc(), Order.id)
        )
    )


async def load_order_detail(
    session: AsyncSession, context: RequestContext, order_id: UUID
) -> tuple[Order, list[OrderItem], list[OrderStatusHistory]]:
    order = await load_order(session, context, order_id)
    return order, await _load_items(session, order.id), await _load_history(session, order.id)


async def load_order_maps(
    session: AsyncSession, orders: list[Order]
) -> dict[UUID, tuple[list[OrderItem], list[OrderStatusHistory]]]:
    """Items and history for many orders in two queries, not two per order."""
    order_ids = [order.id for order in orders]
    if not order_ids:
        return {}
    items_by_order: dict[UUID, list[OrderItem]] = {}
    for item in await session.scalars(
        select(OrderItem).where(OrderItem.order_id.in_(order_ids)).order_by(OrderItem.id)
    ):
        items_by_order.setdefault(item.order_id, []).append(item)
    history_by_order: dict[UUID, list[OrderStatusHistory]] = {}
    for entry in await session.scalars(
        select(OrderStatusHistory)
        .where(OrderStatusHistory.order_id.in_(order_ids))
        .order_by(OrderStatusHistory.created_at, OrderStatusHistory.id)
    ):
        history_by_order.setdefault(entry.order_id, []).append(entry)
    return {
        order_id: (items_by_order.get(order_id, []), history_by_order.get(order_id, []))
        for order_id in order_ids
    }


async def _order_body(session: AsyncSession, order: Order) -> dict:
    items = await _load_items(session, order.id)
    history = await _load_history(session, order.id)
    return {
        "id": str(order.id),
        "organizationId": str(order.organization_id),
        "storeId": str(order.store_id),
        "customerId": str(order.customer_id) if order.customer_id else None,
        "status": OrderStatus(order.status).value,
        "subtotal": str(order.subtotal),
        "total": str(order.total),
        "prescriptionRequired": bool(order.prescription_required),
        "deliveryAddress": order.delivery_address,
        "createdAt": order.created_at.isoformat() if order.created_at else None,
        "items": [
            {
                "id": str(item.id),
                "storeProductId": str(item.store_product_id),
                "productName": item.product_name,
                "quantity": str(item.quantity),
                "unitPrice": str(item.unit_price),
                "lineTotal": str(item.line_total),
            }
            for item in items
        ],
        "history": [
            {
                "id": str(entry.id),
                "fromStatus": entry.from_status.value if entry.from_status else None,
                "toStatus": entry.to_status.value,
                "actorUserId": str(entry.actor_user_id) if entry.actor_user_id else None,
                "createdAt": entry.created_at.isoformat(),
            }
            for entry in history
        ],
    }


async def create_order(
    session: AsyncSession,
    context: RequestContext,
    payload,
    *,
    idempotency_key: str,
    request_id: str,
):
    """Staff-placed checkout on behalf of a phone or walk-in customer."""
    if context.role not in STAFF_ROLES:
        raise Forbidden("Only staff may create orders")
    return await _place_order(
        session,
        organization_id=context.organization_id,
        store_id=_store_id(context),
        actor_user_id=context.user_id,
        payload=payload,
        idempotency_key=idempotency_key,
        request_id=request_id,
    )


async def create_guest_order(
    session: AsyncSession,
    *,
    organization_id: UUID,
    store_id: UUID,
    payload,
    idempotency_key: str,
    request_id: str,
) -> tuple[Order, list[OrderItem], list[OrderStatusHistory]]:
    """Anonymous storefront checkout.

    The tenant and branch come from the resolved storefront, never from a
    token, so the only trust placed in the caller is that it picked a real,
    enabled storefront. Prescription gating is untouched: a prescription-
    required order still cannot be accepted until a pharmacist approves.
    """
    return await _place_order(
        session,
        organization_id=organization_id,
        store_id=store_id,
        actor_user_id=None,
        payload=payload,
        idempotency_key=idempotency_key,
        request_id=request_id,
    )


async def _place_order(
    session: AsyncSession,
    *,
    organization_id: UUID,
    store_id: UUID,
    actor_user_id: UUID | None,
    payload,
    idempotency_key: str,
    request_id: str,
):
    """Guest checkout: priced from live listings, stock reserved in one transaction.

    A customer is never required. Every line must come from a listed online
    setting on this branch; the online price (when set) wins over the POS shelf
    price because that is what the customer agreed to at checkout.
    """
    # Inventory helpers scope themselves by organization/store; the actor is
    # only carried into audit and history columns, both of which accept None.
    stock_context = RequestContext(
        organization_id=organization_id,
        user_id=actor_user_id,
        role=None,
        store_id=store_id,
    )

    payload_dict = payload.model_dump(by_alias=True)
    stored = await replay(session, organization_id, idempotency_key, payload_dict)
    if stored is not None:
        order = await session.get(Order, UUID(stored["id"]))
        return order, await _load_items(session, order.id), await _load_history(session, order.id)

    # Expired holds from abandoned checkouts go back on the shelf before this
    # one allocates, inside the same transaction.
    await inventory_service.release_expired_reservations(
        session, organization_id=organization_id, store_id=store_id
    )

    if payload.customer_id is not None:
        customer = await session.get(Customer, payload.customer_id)
        if customer is None or customer.organization_id != organization_id:
            raise NotFound("Customer not found")
    if payload.fulfillment == "delivery" and not payload.delivery_address:
        raise ValidationError("Delivery orders require a delivery address")

    # Resolve every line against the branch's live listing before any write.
    lines: list[tuple[object, StoreProduct, Decimal, Decimal]] = []
    subtotal = Decimal(0)
    prescription_required = False
    for line in payload.items:
        row = (
            await session.execute(
                select(StoreProduct, EcommerceProductSetting, PharmacyProduct, CatalogProduct)
                .join(
                    EcommerceProductSetting,
                    EcommerceProductSetting.store_product_id == StoreProduct.id,
                )
                .join(PharmacyProduct, PharmacyProduct.id == StoreProduct.pharmacy_product_id)
                .join(
                    CatalogProduct,
                    CatalogProduct.id == PharmacyProduct.catalog_product_id,
                    isouter=True,
                )
                .where(
                    StoreProduct.id == line.store_product_id,
                    StoreProduct.store_id == store_id,
                    StoreProduct.organization_id == organization_id,
                )
            )
        ).first()
        if row is None or not row[1].listed:
            raise ValidationError(f"Store product '{line.store_product_id}' is not listed online")
        store_product, setting, pharmacy_product, catalog_product = row
        if not store_product.active:
            raise ValidationError(f"Store product '{store_product.sku}' is no longer active")
        if payload.fulfillment == "pickup" and not setting.pickup_enabled:
            raise ValidationError(f"Store product '{store_product.sku}' does not support pickup")
        if payload.fulfillment == "delivery" and not setting.delivery_enabled:
            raise ValidationError(f"Store product '{store_product.sku}' does not support delivery")
        unit_price = Decimal(setting.online_price or store_product.sale_price).quantize(CENT)
        line_total = (Decimal(line.quantity) * unit_price).quantize(CENT)
        subtotal += line_total
        prescription_required = prescription_required or bool(
            catalog_product is not None and catalog_product.prescription_required
        )
        lines.append((line, store_product, unit_price, line_total))

    # Allocate every line first so one shortfall aborts before any write.
    allocations_by_line: list[list] = []
    for line, store_product, _unit_price, _line_total in lines:
        result = await inventory_service.allocate_fefo_for_product(
            session, stock_context, store_product.id, line.quantity
        )
        if not result.ok:
            raise inventory_service.InsufficientStock(
                f"Insufficient stock for store product '{store_product.sku}'"
            )
        allocations_by_line.append(result.allocations)

    now = utc_now()
    order = Order(
        organization_id=organization_id,
        store_id=store_id,
        customer_id=payload.customer_id,
        status=OrderStatus.RESERVED,
        subtotal=subtotal.quantize(CENT),
        total=subtotal.quantize(CENT),
        idempotency_key=idempotency_key,
        delivery_address=payload.delivery_address,
        prescription_required=prescription_required,
        created_at=now,
        updated_at=now,
    )
    session.add(order)
    await session.flush()

    for (line, store_product, unit_price, line_total), allocations in zip(
        lines, allocations_by_line
    ):
        session.add(
            OrderItem(
                order_id=order.id,
                store_product_id=store_product.id,
                product_name=pharmacy_product.name,
                quantity=line.quantity,
                unit_price=unit_price,
                line_total=line_total,
            )
        )
        await _reserve_allocations(session, stock_context, order, store_product, allocations, now)

    session.add(
        OrderStatusHistory(
            organization_id=organization_id,
            store_id=store_id,
            order_id=order.id,
            from_status=None,
            to_status=OrderStatus.RESERVED,
            actor_user_id=actor_user_id,
            created_at=now,
        )
    )
    record_audit(
        session,
        stock_context,
        action="order.created",
        entity_type="order",
        entity_id=order.id,
        request_id=request_id,
        after={"total": str(order.total), "prescriptionRequired": prescription_required},
    )
    body = await _order_body(session, order)
    remember(
        session,
        organization_id,
        idempotency_key,
        payload_dict,
        response_status=201,
        response_body=body,
    )
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise Conflict("Order already exists for this idempotency key") from exc
    except Exception:
        await session.rollback()
        raise
    return order, await _load_items(session, order.id), await _load_history(session, order.id)


async def _reserve_allocations(
    session: AsyncSession,
    context: RequestContext,
    order: Order,
    store_product: StoreProduct,
    allocations: list,
    now,
) -> None:
    """One reservation row per batch plus the reserved projection bump.

    The bump is guarded: ``on_hand - reserved`` must never go below zero, so a
    second order racing for already-held stock fails its whole checkout instead
    of poisoning availability. ``FOR UPDATE`` serializes the read-modify-write
    against any other transaction holding the same balance row.
    """
    for allocation in allocations:
        session.add(
            StockReservation(
                organization_id=order.organization_id,
                store_id=order.store_id,
                store_product_id=store_product.id,
                batch_id=allocation.batch_id,
                reference_type="order",
                reference_id=order.id,
                quantity=allocation.quantity,
                expires_at=now + RESERVATION_TTL,
                created_at=now,
                updated_at=now,
            )
        )
    balance = await session.scalar(
        select(InventoryBalance)
        .where(
            InventoryBalance.store_id == store_product.store_id,
            InventoryBalance.store_product_id == store_product.id,
        )
        .with_for_update()
    )
    assert balance is not None
    balance.reserved = Decimal(balance.reserved) + sum(
        (Decimal(a.quantity) for a in allocations), Decimal(0)
    )
    if Decimal(balance.on_hand) - Decimal(balance.reserved) < 0:
        raise inventory_service.InsufficientStock(
            f"Available stock cannot cover reservation for store product '{store_product.sku}'"
        )


async def _active_reservations(
    session: AsyncSession, order: Order
) -> list[tuple[StockReservation, StoreProduct]]:
    """The order's live holds: neither released nor past their expiry.

    Locked ``FOR UPDATE`` so two transitions racing over the same order (a
    cancel against a completion, or two cancels) cannot both claim the same
    reservation: the loser waits, its re-checked snapshot sees ``released_at``
    already set, and the hold is released exactly once.
    """
    rows = (
        await session.execute(
            select(StockReservation, StoreProduct)
            .join(StoreProduct, StoreProduct.id == StockReservation.store_product_id)
            .where(
                StockReservation.reference_type == "order",
                StockReservation.reference_id == order.id,
                StockReservation.released_at.is_(None),
                or_(
                    StockReservation.expires_at.is_(None),
                    StockReservation.expires_at > utc_now(),
                ),
            )
            .with_for_update()
            .order_by(StockReservation.batch_id)
        )
    ).all()
    return [(reservation, store_product) for reservation, store_product in rows]


async def _release_reservations(session: AsyncSession, pairs: list, now) -> None:
    """Return reserved quantities to availability without touching the ledger.

    A reservation never moved stock -- it only held availability back -- so a
    release just clears the hold; on-hand and movements stay untouched.
    """
    released: dict[UUID, Decimal] = {}
    for reservation, _store_product in pairs:
        reservation.released_at = now
        key = reservation.store_product_id
        released[key] = released.get(key, Decimal(0)) + Decimal(reservation.quantity)
    for store_product_id, quantity in released.items():
        balance = await session.scalar(
            select(InventoryBalance).where(
                InventoryBalance.store_product_id == store_product_id,
                InventoryBalance.store_id == pairs[0][1].store_id,
            )
        )
        assert balance is not None
        # Exact subtraction on purpose: each reservation is released exactly once
        # (the active-set query guards it), so going below zero means a real bug
        # and must surface, not be clamped into silence.
        balance.reserved = Decimal(balance.reserved) - quantity


async def transition_order(
    session: AsyncSession,
    context: RequestContext,
    order_id: UUID,
    target: OrderStatus,
    *,
    request_id: str,
):
    """Apply the explicit transition matrix with prescription gating and side effects.

    Accepting a prescription-required order demands an approved prescription;
    cancelling releases every active reservation; completing converts the order
    into an ONLINE sale exactly once.
    """
    if context.role not in STAFF_ROLES:
        raise Forbidden("Only staff may change orders")
    order = await load_order(session, context, order_id)
    current = OrderStatus(order.status)
    target_status = OrderStatus(target)
    try:
        transition = apply_order_transition(order.id, current, target_status)
    except ValueError as exc:
        raise Conflict(str(exc)) from exc

    if target_status is OrderStatus.ACCEPTED and order.prescription_required:
        approved = await session.scalar(
            select(Prescription.id).where(
                Prescription.order_id == order.id,
                Prescription.status == PrescriptionStatus.APPROVED,
            )
        )
        if approved is None:
            raise Conflict("Order requires an approved prescription before acceptance")

    now = utc_now()
    if target_status is OrderStatus.CANCELLED:
        await _release_reservations(session, await _active_reservations(session, order), now)

    sale = None
    if target_status is OrderStatus.COMPLETED:
        sale = await _convert_to_sale(session, context, order, request_id)

    order.status = target_status
    session.add(
        OrderStatusHistory(
            organization_id=order.organization_id,
            store_id=order.store_id,
            order_id=order.id,
            from_status=current,
            to_status=target_status,
            actor_user_id=context.user_id,
            created_at=now,
        )
    )
    record_audit(
        session,
        context,
        action=f"order.{target_status.value}",
        entity_type="order",
        entity_id=order.id,
        request_id=request_id,
        before={"status": current.value},
        after={"status": target_status.value},
    )
    enqueue_outbox(
        session,
        organization_id=order.organization_id,
        event_type="order.completed" if target_status is OrderStatus.COMPLETED else "order.status_changed",
        aggregate_type="order",
        aggregate_id=order.id,
        payload={
            "order_id": str(order.id),
            "store_id": str(order.store_id),
            "from": current.value,
            "to": target_status.value,
            **({"sale_id": str(sale.id)} if sale is not None else {}),
        },
    )
    await session.commit()
    return order, await _load_items(session, order.id), await _load_history(session, order.id)


async def _convert_to_sale(
    session: AsyncSession,
    context: RequestContext,
    order: Order,
    request_id: str,
) -> Sale:
    """Turn the reserved order into an ONLINE sale, consuming its reservations.

    Idempotency comes from the state machine -- COMPLETED has no outgoing
    transitions -- and the unique ``(organization_id, idempotency_key)`` index on
    sales backstops a concurrent double-completion at the database level.
    """
    pairs = await _active_reservations(session, order)
    by_product: dict[UUID, list] = {}
    for reservation, _store_product in pairs:
        by_product.setdefault(reservation.store_product_id, []).append(reservation)

    items = await _load_items(session, order.id)
    sale = Sale(
        organization_id=order.organization_id,
        store_id=order.store_id,
        customer_id=order.customer_id,
        order_id=order.id,
        channel=SaleChannel.ONLINE,
        status=SaleStatus.COMPLETED,
        subtotal=Decimal(order.subtotal),
        discount=Decimal(0),
        total=Decimal(order.total),
        idempotency_key=f"order:{order.id}",
        receipt_number=None,
        created_at=utc_now(),
    )
    session.add(sale)
    await session.flush()

    for item in items:
        reservations = by_product.get(item.store_product_id, [])
        if sum((Decimal(r.quantity) for r in reservations), Decimal(0)) < Decimal(item.quantity):
            raise Conflict("Order reservations no longer cover every line")
        sale_item = SaleItem(
            sale_id=sale.id,
            store_product_id=item.store_product_id,
            product_name=item.product_name,
            quantity=item.quantity,
            unit_price=Decimal(item.unit_price),
            line_total=Decimal(item.line_total),
        )
        session.add(sale_item)
        await session.flush()
        for reservation in reservations:
            session.add(
                SaleItemBatchAllocation(
                    sale_item_id=sale_item.id,
                    batch_id=reservation.batch_id,
                    quantity=reservation.quantity,
                )
            )
        # Consume the held batches: negative ledger movements plus the on-hand
        # projection, then clear the hold itself.
        allocations = [
            Allocation(batch_id=r.batch_id, quantity=Decimal(r.quantity))
            for r in reservations
        ]
        await inventory_service.consume_allocations(
            session,
            context,
            item.store_product_id,
            allocations,
            InventoryMovementType.SALE,
            reference_type="sale",
            reference_id=sale.id,
        )
        for r in reservations:
            r.released_at = utc_now()
        total_reserved = sum((Decimal(r.quantity) for r in reservations), Decimal(0))
        balance = await session.scalar(
            select(InventoryBalance).where(
                InventoryBalance.store_product_id == item.store_product_id,
                InventoryBalance.store_id == order.store_id,
            )
        )
        assert balance is not None
        balance.reserved = Decimal(balance.reserved) - total_reserved

    record_audit(
        session,
        context,
        action="sale.created",
        entity_type="sale",
        entity_id=sale.id,
        request_id=request_id,
        after=redact({"orderId": str(order.id), "total": str(sale.total), "channel": "online"}),
    )
    return sale


def _store_id(context: RequestContext) -> UUID:
    if context.store_id is None:
        raise ValidationError("Store context required", code="STORE_CONTEXT_REQUIRED")
    return context.store_id

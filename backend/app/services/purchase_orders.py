from __future__ import annotations

from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.context import RequestContext
from app.domains.products import PharmacyProduct, StoreProduct
from app.domains.purchase_orders import PurchaseOrder, PurchaseOrderItem, PurchaseOrderStatus
from app.domains.purchasing import Purchase, PurchaseItem, PurchaseStatus
from app.domains.suppliers import Supplier
from app.errors import Conflict, IdempotencyConflict, NotFound, ValidationError
from app.models import Role
from app.schemas.purchase_orders import (
    PurchaseOrderConvertRequest,
    PurchaseOrderConvertResult,
    PurchaseOrderCreateRequest,
    PurchaseOrderItemCreate,
    PurchaseOrderItemResponse,
    PurchaseOrderItemUpdate,
    PurchaseOrderResponse,
    SkippedLine,
)
from app.security import utc_now
from app.services.audit import enqueue_outbox, record_audit, redact
from app.services.purchasing import create_purchase
from app.services.suppliers import load_supplier

#: PO paperwork is counter work -- every store role, cashiers included. Only the
#: conversion into a purchase draft (which carries supplier costs) is restricted.
CONVERTER_ROLES = frozenset({Role.OWNER, Role.MANAGER})

#: ``PurchaseItem.batch_number`` is NOT NULL and confirm books batches verbatim
#: from it, so converted lines carry this marker until a manager edits the draft.
PENDING_BATCH = "PENDING"


async def load_po(
    session: AsyncSession, context: RequestContext, po_id: UUID, *, for_update: bool = False
) -> PurchaseOrder:
    """A PO of another tenant (or branch) does not exist for the caller.

    ``for_update`` takes a row lock, so a status check made under it holds for
    the whole transaction -- two simultaneous conversions cannot both see the
    order open.
    """
    statement = select(PurchaseOrder).where(PurchaseOrder.id == po_id)
    if for_update:
        statement = statement.with_for_update()
    row = (await session.execute(statement)).scalar_one_or_none()
    if row is None or row.organization_id != context.organization_id:
        raise NotFound("Purchase order not found")
    if context.store_id is not None and row.store_id != context.store_id:
        raise NotFound("Purchase order not found")
    return row


async def _load_items(session: AsyncSession, po_id: UUID) -> list[PurchaseOrderItem]:
    return list(
        await session.scalars(
            select(PurchaseOrderItem)
            .where(PurchaseOrderItem.purchase_order_id == po_id)
            .order_by(PurchaseOrderItem.created_at, PurchaseOrderItem.id)
        )
    )


async def _received_by_item(session: AsyncSession, po_id: UUID) -> dict[UUID, Decimal]:
    rows = await session.execute(
        select(PurchaseItem.purchase_order_item_id, func.sum(PurchaseItem.quantity))
        .join(Purchase, Purchase.id == PurchaseItem.purchase_id)
        .where(
            Purchase.purchase_order_id == po_id,
            Purchase.status == PurchaseStatus.CONFIRMED,
            PurchaseItem.purchase_order_item_id.is_not(None),
        )
        .group_by(PurchaseItem.purchase_order_item_id)
    )
    return {item_id: Decimal(quantity) for item_id, quantity in rows if item_id is not None}


def order_response(
    row: PurchaseOrder,
    items: list[PurchaseOrderItem],
    *,
    received: dict[UUID, Decimal],
    supplier_name: str | None,
    include_items: bool = True,
) -> PurchaseOrderResponse:
    ordered_quantity = sum((Decimal(item.quantity) for item in items), Decimal(0))
    received_quantity = sum((received.get(item.id, Decimal(0)) for item in items), Decimal(0))
    item_responses = []
    if include_items:
        for item in items:
            received_quantity_for_item = received.get(item.id, Decimal(0))
            item_responses.append(
                PurchaseOrderItemResponse(
                    **PurchaseOrderItemResponse.model_validate(item).model_dump(
                        exclude={"received_quantity", "remaining_quantity"}
                    ),
                    received_quantity=received_quantity_for_item,
                    remaining_quantity=max(Decimal(item.quantity) - received_quantity_for_item, Decimal(0)),
                )
            )
    return PurchaseOrderResponse(
        id=row.id,
        organization_id=row.organization_id,
        store_id=row.store_id,
        supplier_id=row.supplier_id,
        supplier_name=supplier_name,
        status=row.status,
        expected_at=row.expected_at,
        note=row.note,
        ordered_at=row.ordered_at,
        closed_at=row.closed_at,
        cancelled_at=row.cancelled_at,
        created_at=row.created_at,
        item_count=len(items),
        ordered_quantity=ordered_quantity,
        received_quantity=received_quantity,
        items=item_responses,
    )


async def response_for_order(
    session: AsyncSession,
    row: PurchaseOrder,
    *,
    include_items: bool,
) -> PurchaseOrderResponse:
    items = await _load_items(session, row.id)
    supplier_name = None
    if row.supplier_id is not None:
        supplier_name = await session.scalar(select(Supplier.name).where(Supplier.id == row.supplier_id))
    return order_response(
        row,
        items,
        received=await _received_by_item(session, row.id),
        supplier_name=supplier_name,
        include_items=include_items,
    )


def _track(
    session: AsyncSession,
    context: RequestContext,
    *,
    action: str,
    row: PurchaseOrder,
    request_id: str,
    after: dict[str, Any],
) -> None:
    """Audit plus outbox parity with the purchasing service."""
    record_audit(
        session,
        context,
        action=action,
        entity_type="purchase_order",
        entity_id=row.id,
        request_id=request_id,
        after=redact(after),
    )
    enqueue_outbox(
        session,
        organization_id=context.organization_id,
        event_type=action,
        aggregate_type="purchase_order",
        aggregate_id=row.id,
        payload={"purchase_order_id": str(row.id), "store_id": str(row.store_id), **after},
    )


async def _load_supplier_optional(
    session: AsyncSession, context: RequestContext, supplier_id: UUID | None
) -> None:
    if supplier_id is not None:
        await load_supplier(session, context, supplier_id)


async def create_order(
    session: AsyncSession,
    context: RequestContext,
    payload: PurchaseOrderCreateRequest,
    *,
    idempotency_key: str,
    request_id: str,
) -> PurchaseOrder:
    if context.store_id is None:
        raise NotFound("Purchase order not found")
    await _load_supplier_optional(session, context, payload.supplier_id)

    row = PurchaseOrder(
        organization_id=context.organization_id,
        store_id=context.store_id,
        supplier_id=payload.supplier_id,
        status=PurchaseOrderStatus.DRAFT,
        expected_at=payload.expected_at,
        note=payload.note,
        idempotency_key=idempotency_key,
    )
    session.add(row)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise IdempotencyConflict("Idempotency key already used") from exc
    for line in payload.items:
        session.add(_item_from_request(row.id, line))
    _track(
        session,
        context,
        action="purchase_order.created",
        row=row,
        request_id=request_id,
        after={
            "status": row.status.value,
            "supplier_id": str(payload.supplier_id) if payload.supplier_id else None,
            "item_count": len(payload.items),
        },
    )
    await session.commit()
    return row


def _item_from_request(po_id: UUID, line: PurchaseOrderItemCreate) -> PurchaseOrderItem:
    return PurchaseOrderItem(
        purchase_order_id=po_id,
        name=line.clean_name,
        quantity=line.quantity,
        est_unit_cost=line.est_unit_cost,
        catalog_product_id=line.catalog_product_id,
        pharmacy_product_id=line.pharmacy_product_id,
    )


async def list_orders(
    session: AsyncSession,
    context: RequestContext,
    *,
    status: PurchaseOrderStatus | None = None,
    limit: int = 25,
    offset: int = 0,
) -> tuple[list[PurchaseOrder], int]:
    scope: tuple[Any, ...] = (PurchaseOrder.organization_id == context.organization_id,)
    if context.store_id is not None:
        scope = (*scope, PurchaseOrder.store_id == context.store_id)
    if status is not None:
        scope = (*scope, PurchaseOrder.status == status)
    total = await session.scalar(select(func.count()).select_from(PurchaseOrder).where(*scope))
    rows = list(
        await session.scalars(
            select(PurchaseOrder)
            .where(*scope)
            .order_by(PurchaseOrder.created_at.desc(), PurchaseOrder.id)
            .limit(limit)
            .offset(offset)
        )
    )
    return rows, int(total or 0)


async def get_with_items(
    session: AsyncSession, context: RequestContext, po_id: UUID
) -> PurchaseOrderResponse:
    row = await load_po(session, context, po_id)
    return await response_for_order(session, row, include_items=True)


async def _draft_or_conflict(row: PurchaseOrder) -> PurchaseOrder:
    if row.status is not PurchaseOrderStatus.DRAFT:
        raise Conflict("Only draft purchase orders can be edited")
    return row


async def add_item(
    session: AsyncSession,
    context: RequestContext,
    po_id: UUID,
    payload: PurchaseOrderItemCreate,
    *,
    request_id: str,
) -> PurchaseOrderItem:
    row = await load_po(session, context, po_id)
    await _draft_or_conflict(row)
    item = _item_from_request(row.id, payload)
    session.add(item)
    _track(
        session,
        context,
        action="purchase_order.updated",
        row=row,
        request_id=request_id,
        after={"change": "item_added", "item_name": item.name},
    )
    await session.commit()
    return item


async def update_item(
    session: AsyncSession,
    context: RequestContext,
    po_id: UUID,
    item_id: UUID,
    payload: PurchaseOrderItemUpdate,
    *,
    request_id: str,
) -> PurchaseOrderItem:
    row = await load_po(session, context, po_id)
    await _draft_or_conflict(row)
    item = await session.get(PurchaseOrderItem, item_id)
    if item is None or item.purchase_order_id != row.id:
        raise NotFound("Purchase order item not found")
    # exclude_unset only: an explicit null clears est_unit_cost, which
    # exclude_none would silently swallow.
    changes = payload.model_dump(exclude_unset=True)
    for field in ("name", "quantity", "est_unit_cost"):
        if field in changes:
            setattr(item, field, changes[field])
    _track(
        session,
        context,
        action="purchase_order.updated",
        row=row,
        request_id=request_id,
        after={"change": "item_updated", "item_name": item.name},
    )
    await session.commit()
    return item


async def remove_item(
    session: AsyncSession,
    context: RequestContext,
    po_id: UUID,
    item_id: UUID,
    *,
    request_id: str,
) -> PurchaseOrderItem:
    row = await load_po(session, context, po_id)
    await _draft_or_conflict(row)
    item = await session.get(PurchaseOrderItem, item_id)
    if item is None or item.purchase_order_id != row.id:
        raise NotFound("Purchase order item not found")
    name = item.name
    await session.delete(item)
    _track(
        session,
        context,
        action="purchase_order.updated",
        row=row,
        request_id=request_id,
        after={"change": "item_removed", "item_name": name},
    )
    await session.commit()
    return item


async def mark_ordered(
    session: AsyncSession, context: RequestContext, po_id: UUID, *, request_id: str
) -> PurchaseOrder:
    row = await load_po(session, context, po_id)
    if row.status is not PurchaseOrderStatus.DRAFT:
        raise Conflict("Only draft purchase orders can be marked ordered")
    row.status = PurchaseOrderStatus.ORDERED
    row.ordered_at = utc_now()
    _track(
        session,
        context,
        action="purchase_order.ordered",
        row=row,
        request_id=request_id,
        after={"status": row.status.value},
    )
    await session.commit()
    return row


async def close_order(
    session: AsyncSession, context: RequestContext, po_id: UUID, *, request_id: str
) -> PurchaseOrder:
    row = await load_po(session, context, po_id)
    if row.status not in (PurchaseOrderStatus.ORDERED, PurchaseOrderStatus.PARTIALLY_RECEIVED):
        raise Conflict("Only ordered or partially received purchase orders can be closed")
    row.status = PurchaseOrderStatus.CLOSED
    row.closed_at = utc_now()
    _track(
        session,
        context,
        action="purchase_order.closed",
        row=row,
        request_id=request_id,
        after={"status": row.status.value},
    )
    await session.commit()
    return row


async def cancel_order(
    session: AsyncSession, context: RequestContext, po_id: UUID, *, request_id: str
) -> PurchaseOrder:
    row = await load_po(session, context, po_id)
    if row.status not in (PurchaseOrderStatus.DRAFT, PurchaseOrderStatus.ORDERED):
        raise Conflict("Only open purchase orders can be cancelled")
    row.status = PurchaseOrderStatus.CANCELLED
    row.cancelled_at = utc_now()
    _track(
        session,
        context,
        action="purchase_order.cancelled",
        row=row,
        request_id=request_id,
        after={"status": row.status.value},
    )
    await session.commit()
    return row


# --- conversion into a purchase draft ------------------------------------------


async def _resolve_store_product(
    session: AsyncSession, store_id: UUID, organization_id: UUID, item: PurchaseOrderItem
) -> StoreProduct | None:
    product_ids: list[UUID] = []
    if item.pharmacy_product_id is not None:
        product_ids.append(item.pharmacy_product_id)
    if item.catalog_product_id is not None:
        linked = await session.scalar(
            select(PharmacyProduct.id).where(
                PharmacyProduct.organization_id == organization_id,
                PharmacyProduct.catalog_product_id == item.catalog_product_id,
                PharmacyProduct.active.is_(True),
            )
        )
        if linked is not None:
            product_ids.append(linked)
    if not product_ids:
        return None
    return await session.scalar(
        select(StoreProduct).where(
            StoreProduct.store_id == store_id,
            StoreProduct.active.is_(True),
            StoreProduct.pharmacy_product_id.in_(product_ids),
        )
    )


async def convert_to_purchase(
    session: AsyncSession,
    context: RequestContext,
    po_id: UUID,
    payload: PurchaseOrderConvertRequest,
    *,
    request_id: str,
) -> PurchaseOrderConvertResult:
    """Turn resolvable lines into a purchase draft, atomically with closing.

    Lines that cannot be resolved to an active shelf product at this store are
    reported back as ``skipped`` rather than blocking the rest; free-text lines
    without any link always skip. The whole thing commits once -- a half-created
    draft with an open PO would invite converting twice.
    """
    if context.role not in CONVERTER_ROLES:
        raise Conflict("Only owners and managers may convert a purchase order")
    if context.store_id is None:
        raise NotFound("Purchase order not found")
    # Locked for the whole transaction: the status check below holds while the
    # purchase draft is built, so a concurrent convert blocks here, then sees
    # the order closed and fails instead of double-creating the draft.
    row = await load_po(session, context, po_id, for_update=True)
    if row.status not in (PurchaseOrderStatus.DRAFT, PurchaseOrderStatus.ORDERED):
        raise Conflict("Only open purchase orders can be converted")

    items = await _load_items(session, row.id)
    resolved = []
    skipped: list[SkippedLine] = []
    for item in items:
        store_product = await _resolve_store_product(
            session, row.store_id, context.organization_id, item
        )
        if store_product is None:
            skipped.append(
                SkippedLine(
                    item_id=item.id,
                    name=item.name,
                    reason="No active shelf product for this line at this store",
                )
            )
            continue
        resolved.append((item, store_product))

    if not resolved:
        raise Conflict("Nothing on this order resolves to a shelf product at this store")

    from app.schemas.purchasing import PurchaseCreateRequest

    supplier_id = payload.supplier_id if payload.supplier_id is not None else row.supplier_id
    if supplier_id is None:
        raise ValidationError(
            "A supplier is required to convert: pass supplierId or set one on the order"
        )

    lines = [
        {
            "storeProductId": str(store_product.id),
            "quantity": str(Decimal(item.quantity)),
            "unitCost": str(Decimal(item.est_unit_cost)) if item.est_unit_cost is not None else "0.00",
            "batchNumber": PENDING_BATCH,
        }
        for item, store_product in resolved
    ]
    purchase_request = PurchaseCreateRequest.model_validate(
        {"supplierId": str(supplier_id), "note": f"From PO {str(row.id)[:8]}", "items": lines}
    )
    # commit=False keeps the draft, the closure, and both audit trails in one
    # transaction; create_purchase re-checks roles, supplier, and shelf rows.
    purchase = await create_purchase(
        session, context, purchase_request, request_id=request_id, commit=False
    )

    was_draft = row.status is PurchaseOrderStatus.DRAFT
    row.status = PurchaseOrderStatus.CLOSED
    row.closed_at = utc_now()
    _track(
        session,
        context,
        action="purchase_order.closed",
        row=row,
        request_id=request_id,
        after={
            "status": row.status.value,
            "convertedToPurchaseId": str(purchase.id),
            "convertedCount": len(resolved),
            "skippedCount": len(skipped),
            "convertedFromDraft": was_draft,
        },
    )
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise Conflict("Conversion could not be completed") from exc
    except Exception:
        await session.rollback()
        raise
    return PurchaseOrderConvertResult(
        purchase_id=purchase.id,
        purchase_order_id=row.id,
        converted_count=len(resolved),
        skipped=skipped,
    )

from __future__ import annotations

from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.context import RequestContext
from app.domains.inventory import InventoryMovementType
from app.domains.purchasing import Purchase, PurchaseItem, PurchaseStatus
from app.errors import Conflict, NotFound, ValidationError
from app.models import Role
from app.schemas.purchasing import PurchaseCreateRequest, PurchaseReturnRequest
from app.security import utc_now
from app.services.audit import enqueue_outbox, record_audit, redact
from app.services.inventory import (
    InsufficientStock,
    allocate_fefo_for_product,
    consume_allocations,
    load_store_product,
)
from app.services.suppliers import append_ledger_entry, load_supplier

WRITER_ROLES = frozenset({Role.OWNER, Role.MANAGER})
COST_ROLES = frozenset({Role.OWNER, Role.MANAGER})

RETURN_OF_PREFIX = "return-of:"


def can_see_costs(context: RequestContext) -> bool:
    return context.role in COST_ROLES


async def load_purchase(
    session: AsyncSession, context: RequestContext, purchase_id: UUID
) -> Purchase:
    """A purchase of another tenant (or branch) does not exist for the caller."""
    purchase = await session.get(Purchase, purchase_id)
    if purchase is None or purchase.organization_id != context.organization_id:
        raise NotFound("Purchase not found")
    if context.store_id is not None and purchase.store_id != context.store_id:
        raise NotFound("Purchase not found")
    return purchase


async def _load_items(session: AsyncSession, purchase_id: UUID) -> list[PurchaseItem]:
    return list(
        await session.scalars(
            select(PurchaseItem)
            .where(PurchaseItem.purchase_id == purchase_id)
            .order_by(PurchaseItem.id)
        )
    )


async def create_purchase(
    session: AsyncSession,
    context: RequestContext,
    payload: PurchaseCreateRequest,
    *,
    request_id: str,
) -> Purchase:
    """Create a DRAFT with server-computed totals; nothing touches stock yet."""
    if context.role not in WRITER_ROLES:
        raise Conflict("Only owners and managers may create purchases")
    if context.store_id is None:
        raise NotFound("Purchase not found")
    await load_supplier(session, context, payload.supplier_id)
    for item in payload.items:
        await load_store_product(session, context, item.store_product_id)

    total = sum((item.quantity * item.unit_cost for item in payload.items), Decimal("0"))
    purchase = Purchase(
        organization_id=context.organization_id,
        store_id=context.store_id,
        supplier_id=payload.supplier_id,
        status=PurchaseStatus.DRAFT,
        invoice_number=payload.invoice_number,
        note=payload.note,
        purchased_at=payload.purchased_at or utc_now().date(),
        total_amount=total.quantize(Decimal("0.01")),
        idempotency_key=f"draft:{utc_now().isoformat()}",
    )
    session.add(purchase)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise Conflict("Purchase could not be created") from exc
    for item in payload.items:
        session.add(
            PurchaseItem(
                purchase_id=purchase.id,
                store_product_id=item.store_product_id,
                quantity=item.quantity,
                unit_cost=item.unit_cost,
                batch_number=item.batch_number.strip(),
                expiry_date=item.expiry_date,
            )
        )
    record_audit(
        session,
        context,
        action="purchase.created",
        entity_type="purchase",
        entity_id=purchase.id,
        request_id=request_id,
        after=redact({"supplier_id": str(payload.supplier_id), "total": str(total)}),
    )
    await session.commit()
    return purchase


async def confirm_purchase(
    session: AsyncSession,
    context: RequestContext,
    purchase_id: UUID,
    *,
    idempotency_key: str,
    request_id: str,
) -> Purchase:
    """Confirm a draft in one atomic transaction.

    Receives every item as an inventory batch, books the supplier liability,
    audits, and stages an outbox event. Any failure rolls the whole thing back.
    """
    if context.role not in WRITER_ROLES:
        raise Conflict("Only owners and managers may confirm purchases")
    purchase = await load_purchase(session, context, purchase_id)

    replay = purchase.status is PurchaseStatus.CONFIRMED and purchase.idempotency_key == idempotency_key
    if not replay:
        clash = await session.scalar(
            select(Purchase.id).where(
                Purchase.organization_id == context.organization_id,
                Purchase.idempotency_key == idempotency_key,
                Purchase.id != purchase.id,
            )
        )
        if clash is not None or purchase.status is PurchaseStatus.CONFIRMED:
            raise Conflict("Purchase already confirmed or idempotency key already used")

    items = await _load_items(session, purchase.id)
    now = utc_now()
    purchase.status = PurchaseStatus.CONFIRMED
    purchase.confirmed_at = now
    purchase.idempotency_key = idempotency_key

    for item in items:
        await receive_for_item(
            session, context, purchase, item, idempotency_key=idempotency_key, request_id=request_id
        )

    await append_ledger_entry(
        session,
        context,
        purchase.supplier_id,
        "purchase",
        Decimal(purchase.total_amount),
        reference_type="purchase",
        reference_id=purchase.id,
        idempotency_key=f"purchase-confirm:{idempotency_key}",
        commit=False,
        request_id=request_id,
    )
    record_audit(
        session,
        context,
        action="purchase.confirmed",
        entity_type="purchase",
        entity_id=purchase.id,
        request_id=request_id,
        after=redact({"supplier_id": str(purchase.supplier_id), "total": str(purchase.total_amount)}),
    )
    enqueue_outbox(
        session,
        organization_id=context.organization_id,
        event_type="purchase.confirmed",
        aggregate_type="purchase",
        aggregate_id=purchase.id,
        payload={
            "purchase_id": str(purchase.id),
            "store_id": str(purchase.store_id),
            "supplier_id": str(purchase.supplier_id),
            "total_amount": str(purchase.total_amount),
            "item_count": len(items),
        },
    )
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise Conflict("Purchase already confirmed") from exc
    except Exception:
        await session.rollback()
        raise
    return purchase


async def receive_for_item(
    session: AsyncSession,
    context: RequestContext,
    purchase: Purchase,
    item: PurchaseItem,
    *,
    idempotency_key: str,
    request_id: str,
) -> None:
    from app.services.inventory import receive_batch

    await receive_batch(
        session,
        context,
        item.store_product_id,
        batch_number=item.batch_number,
        expiry_date=item.expiry_date,
        unit_cost=Decimal(item.unit_cost),
        quantity=Decimal(item.quantity),
        reference_type="purchase",
        reference_id=purchase.id,
        idempotency_key=f"{idempotency_key}:{item.id}",
        commit=False,
        request_id=request_id,
    )


async def _returned_quantities(
    session: AsyncSession, original: Purchase
) -> dict[UUID, Decimal]:
    """Aggregate quantities already returned per store product via prior returns."""
    prior = await session.scalars(
        select(Purchase).where(
            Purchase.organization_id == original.organization_id,
            Purchase.store_id == original.store_id,
            Purchase.status == PurchaseStatus.RETURNED,
            Purchase.note == f"{RETURN_OF_PREFIX}{original.id}",
        )
    )
    returned: dict[UUID, Decimal] = {}
    for ret in prior:
        for item in await _load_items(session, ret.id):
            returned[item.store_product_id] = returned.get(item.store_product_id, Decimal(0)) + Decimal(item.quantity)
    return returned


async def return_purchase(
    session: AsyncSession,
    context: RequestContext,
    purchase_id: UUID,
    payload: PurchaseReturnRequest,
    *,
    request_id: str,
) -> Purchase:
    """Return lines of a confirmed purchase back to the supplier.

    Reduces stock FEFO (never below zero), books a negative supplier ledger
    entry, and records the return as its own RETURNED purchase.
    """
    if context.role not in WRITER_ROLES:
        raise Conflict("Only owners and managers may create returns")
    original = await load_purchase(session, context, purchase_id)
    if original.status is not PurchaseStatus.CONFIRMED:
        raise Conflict("Only confirmed purchases can be returned")

    items = {item.id: item for item in await _load_items(session, original.id)}
    returned = await _returned_quantities(session, original)

    by_product: dict[UUID, Decimal] = {}
    total = Decimal("0")
    for line in payload.lines:
        item = items.get(line.purchase_item_id)
        if item is None:
            raise NotFound("Purchase item not found")
        already = returned.get(item.store_product_id, Decimal(0)) + by_product.get(item.store_product_id, Decimal(0))
        if Decimal(item.quantity) - already < Decimal(line.quantity):
            raise Conflict("Return quantity exceeds remaining purchasable quantity")
        by_product[item.store_product_id] = by_product.get(item.store_product_id, Decimal(0)) + Decimal(line.quantity)
        total += Decimal(line.quantity) * Decimal(item.unit_cost)

    allocations_by_product: dict[UUID, list[Any]] = {}
    for store_product_id, quantity in by_product.items():
        result = await allocate_fefo_for_product(session, context, store_product_id, quantity)
        if not result.ok:
            raise Conflict("Insufficient stock to process the return")
        allocations_by_product[store_product_id] = result.allocations

    return_purchase_row = Purchase(
        organization_id=context.organization_id,
        store_id=context.store_id,
        supplier_id=original.supplier_id,
        status=PurchaseStatus.RETURNED,
        note=f"{RETURN_OF_PREFIX}{original.id}",
        purchased_at=utc_now().date(),
        total_amount=(-total).quantize(Decimal("0.01")),
        idempotency_key=f"return:{utc_now().isoformat()}",
    )
    session.add(return_purchase_row)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise Conflict("Return could not be created") from exc
    for store_product_id, quantity in by_product.items():
        session.add(
            PurchaseItem(
                purchase_id=return_purchase_row.id,
                store_product_id=store_product_id,
                quantity=quantity,
                unit_cost=Decimal(0),
                batch_number="RETURN",
                expiry_date=None,
            )
        )
    for store_product_id, allocations in allocations_by_product.items():
        try:
            await consume_allocations(
                session,
                context,
                store_product_id,
                allocations,
                InventoryMovementType.RETURN,
                reference_type="purchase_return",
                reference_id=original.id,
                commit=False,
            )
        except InsufficientStock as exc:
            await session.rollback()
            raise Conflict("Insufficient stock to process the return") from exc

    await append_ledger_entry(
        session,
        context,
        original.supplier_id,
        "purchase_return",
        (-total).quantize(Decimal("0.01")),
        reference_type="purchase_return",
        reference_id=original.id,
        idempotency_key=f"purchase-return:{return_purchase_row.id}",
        commit=False,
        request_id=request_id,
    )
    record_audit(
        session,
        context,
        action="purchase.returned",
        entity_type="purchase",
        entity_id=original.id,
        request_id=request_id,
        after=redact({"return_purchase_id": str(return_purchase_row.id), "total": str(total)}),
    )
    await session.commit()
    return return_purchase_row


async def list_purchases(
    session: AsyncSession,
    context: RequestContext,
    *,
    status: PurchaseStatus | None = None,
    supplier_id: UUID | None = None,
    limit: int = 25,
    offset: int = 0,
) -> tuple[list[Purchase], int]:
    scope: tuple[Any, ...] = (Purchase.organization_id == context.organization_id,)
    if context.store_id is not None:
        scope = (*scope, Purchase.store_id == context.store_id)
    if status is not None:
        scope = (*scope, Purchase.status == status)
    if supplier_id is not None:
        scope = (*scope, Purchase.supplier_id == supplier_id)
    total = await session.scalar(select(func.count()).select_from(Purchase).where(*scope))
    rows = list(
        await session.scalars(
            select(Purchase)
            .where(*scope)
            .order_by(Purchase.created_at.desc(), Purchase.id)
            .limit(limit)
            .offset(offset)
        )
    )
    return rows, int(total or 0)


async def get_purchase_with_items(
    session: AsyncSession, context: RequestContext, purchase_id: UUID
) -> tuple[Purchase, list[PurchaseItem]]:
    purchase = await load_purchase(session, context, purchase_id)
    return purchase, await _load_items(session, purchase.id)


def require_idempotency_key(idempotency_key: str | None) -> str:
    """Shared header guard; mirrors the FastAPI dependency in ``routers.purchasing``."""
    if not idempotency_key or not 16 <= len(idempotency_key.strip()) <= 128:
        raise ValidationError("Idempotency-Key must be between 16 and 128 characters")
    return idempotency_key.strip()

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.context import RequestContext
from app.domains.inventory import (
    Allocation,
    BatchStock,
    InventoryBalance,
    InventoryBatch,
    InventoryMovement,
    InventoryMovementType,
    StockTransfer,
    StockTransferItem,
    TransferStatus,
    allocate_fefo,
    rebuild_balances,
)
from app.errors import Conflict, DomainError, Forbidden, NotFound, ValidationError
from app.models import Role, StoreProduct
from app.security import utc_now
from app.services.audit import record_audit, redact
from app.services.stores import business_date, load_store

#: Receiving stock is inventory work; only adjustments escalate to OWNER/MANAGER.
INVENTORY_ROLES = frozenset({Role.OWNER, Role.MANAGER, Role.INVENTORY_STAFF})
ADJUSTMENT_ROLES = frozenset({Role.OWNER, Role.MANAGER})


class InsufficientStock(DomainError):
    code = "INSUFFICIENT_STOCK"


@dataclass(frozen=True)
class AllocationResult:
    """Explicit partial/failure result instead of a silent short allocation."""

    requested: Decimal
    allocations: list[Allocation] = field(default_factory=list)

    @property
    def allocated(self) -> Decimal:
        return sum((a.quantity for a in self.allocations), Decimal(0))

    @property
    def shortfall(self) -> Decimal:
        return self.requested - self.allocated

    @property
    def ok(self) -> bool:
        return self.shortfall <= 0


async def load_store_product(
    session: AsyncSession, context: RequestContext, store_product_id: UUID
) -> StoreProduct:
    """A store product of another tenant (or branch) does not exist for the caller."""
    store_product = await session.get(StoreProduct, store_product_id)
    if store_product is None or store_product.organization_id != context.organization_id:
        raise NotFound("Store product not found")
    if context.store_id is not None and store_product.store_id != context.store_id:
        raise NotFound("Store product not found")
    return store_product


def assert_inventory_role(context: RequestContext) -> None:
    if context.role not in INVENTORY_ROLES:
        raise Forbidden("Inventory capability denied")


def assert_adjustment_role(context: RequestContext) -> None:
    if context.role not in ADJUSTMENT_ROLES:
        raise Forbidden("Adjustment requires owner or manager")


# --- projections ------------------------------------------------------------


async def _balance_for(session: AsyncSession, store_product: StoreProduct) -> InventoryBalance:
    balance = await session.scalar(
        select(InventoryBalance).where(
            InventoryBalance.store_id == store_product.store_id,
            InventoryBalance.store_product_id == store_product.id,
        )
    )
    if balance is None:
        balance = InventoryBalance(
            organization_id=store_product.organization_id,
            store_id=store_product.store_id,
            store_product_id=store_product.id,
            on_hand=Decimal(0),
            reserved=Decimal(0),
        )
        session.add(balance)
        await session.flush()
    return balance


def _available(balance: InventoryBalance) -> Decimal:
    return Decimal(balance.on_hand) - Decimal(balance.reserved)


async def _apply_to_balance(
    session: AsyncSession, store_product: StoreProduct, delta: Decimal
) -> InventoryBalance:
    """Single-transaction projection update; never lets available go negative."""
    balance = await _balance_for(session, store_product)
    balance.on_hand = Decimal(balance.on_hand) + delta
    if _available(balance) < 0:
        raise InsufficientStock(
            f"Stock would go negative for store product '{store_product.sku}'"
        )
    return balance


async def _batch_available_map(
    session: AsyncSession, store_product: StoreProduct
) -> dict[UUID, Decimal]:
    rows = await session.execute(
        select(InventoryMovement.batch_id, func.sum(InventoryMovement.quantity))
        .where(
            InventoryMovement.store_product_id == store_product.id,
            InventoryMovement.batch_id.is_not(None),
        )
        .group_by(InventoryMovement.batch_id)
    )
    return {batch_id: Decimal(total or 0) for batch_id, total in rows.all()}


# --- receiving --------------------------------------------------------------


async def receive_batch(
    session: AsyncSession,
    context: RequestContext,
    store_product_id: UUID,
    *,
    batch_number: str,
    expiry_date: date | None = None,
    unit_cost: Decimal = Decimal(0),
    quantity: Decimal,
    reference_type: str | None = None,
    reference_id: UUID | None = None,
    idempotency_key: str,
    request_id: str = "unknown",
    commit: bool = False,
) -> tuple[InventoryBatch, InventoryMovement, InventoryBalance]:
    """Create a batch plus its receipt movement and bump the balance projection.

    Idempotent on ``(store, idempotency_key)``: a repeated key returns the
    original state without creating a second batch or movement. No commit happens
    here so purchasing can compose this into a larger transaction.
    """
    assert_inventory_role(context)
    if quantity <= 0:
        raise ValidationError("Receipt quantity must be positive")
    store_product = await load_store_product(session, context, store_product_id)

    existing = await session.scalar(
        select(InventoryMovement).where(
            InventoryMovement.store_id == store_product.store_id,
            InventoryMovement.idempotency_key == idempotency_key,
        )
    )
    if existing is not None:
        if existing.store_product_id != store_product_id or existing.quantity <= 0:
            raise ValidationError(
                "Idempotency key already used for a different operation",
                code="IDEMPOTENCY_CONFLICT",
            )
        batch = await session.get(InventoryBatch, existing.batch_id)
        if batch is None:
            raise NotFound("Batch not found")
        balance = await _balance_for(session, store_product)
        return batch, existing, balance

    now = utc_now()
    batch = InventoryBatch(
        organization_id=store_product.organization_id,
        store_id=store_product.store_id,
        store_product_id=store_product.id,
        batch_number=batch_number.strip(),
        expiry_date=expiry_date,
        unit_cost=unit_cost,
        received_at=now,
        active=True,
    )
    session.add(batch)
    await session.flush()
    movement = InventoryMovement(
        organization_id=store_product.organization_id,
        store_id=store_product.store_id,
        store_product_id=store_product.id,
        batch_id=batch.id,
        movement_type=InventoryMovementType.RECEIPT,
        quantity=quantity,
        reference_type=reference_type,
        reference_id=reference_id,
        idempotency_key=idempotency_key,
        occurred_at=now,
        actor_user_id=context.user_id,
    )
    session.add(movement)
    balance = await _apply_to_balance(session, store_product, quantity)
    record_audit(
        session,
        context,
        action="inventory.batch_received",
        entity_type="inventory_batch",
        entity_id=batch.id,
        request_id=request_id,
        after=redact(
            {
                "store_product_id": str(store_product_id),
                "batch_number": batch.batch_number,
                "quantity": str(quantity),
            }
        ),
    )
    if commit:
        await session.commit()
    return batch, movement, balance


# --- FEFO allocation + consumption ------------------------------------------


async def list_batches_fefo(
    session: AsyncSession, context: RequestContext, store_product_id: UUID, *, as_of: date | None = None
) -> list[BatchStock]:
    """Batches in FEFO order with their computed available quantity.

    Expired batches are *not* filtered here -- ``allocate_fefo`` decides eligibility
    so that stock-on-hand views can still show what is on the shelf awaiting
    disposal. Callers wanting only dispensable stock should allocate.
    """
    store_product = await load_store_product(session, context, store_product_id)
    batches = list(
        await session.scalars(
            select(InventoryBatch).where(InventoryBatch.store_product_id == store_product.id)
        )
    )
    available_by_batch = await _batch_available_map(session, store_product)
    stocks = [
        BatchStock(
            batch_id=batch.id,
            available=available_by_batch.get(batch.id, Decimal(0)),
            expiry_date=batch.expiry_date,
            received_at=batch.received_at,
        )
        for batch in batches
        if batch.active
    ]
    stocks.sort(
        key=lambda s: (
            s.expiry_date is None,
            s.expiry_date or date.max,
            s.received_at,
            s.batch_id,
        )
    )
    return stocks


async def allocate_fefo_for_product(
    session: AsyncSession,
    context: RequestContext,
    store_product_id: UUID,
    quantity: Decimal,
    *,
    as_of: date | None = None,
) -> AllocationResult:
    """Allocate unexpired stock FEFO without mutating anything.

    Returns an explicit shortfall so callers can decide between failing and
    partially consuming; it never permits negative stock.

    Eligibility is judged on the *branch's* calendar day. On UTC, a store east of
    Greenwich spends the hours after its own midnight still reading yesterday's
    date, and a batch that expired overnight stays dispensable -- for ``Asia/Dhaka``
    that is every night between 00:00 and 06:00 local. Expired medicine reaching a
    patient is the failure this ordering exists to prevent, so the cutoff has to
    come from the same clock the shop is working to.
    """
    if quantity < 0:
        raise ValidationError("Requested quantity cannot be negative")
    store_product = await load_store_product(session, context, store_product_id)
    store = await load_store(session, context, store_product.store_id)
    cutoff = as_of or business_date(store)
    stocks = await list_batches_fefo(session, context, store_product_id, as_of=cutoff)
    allocations, _remaining = allocate_fefo(stocks, quantity, cutoff)
    return AllocationResult(requested=quantity, allocations=allocations)


async def consume_allocations(
    session: AsyncSession,
    context: RequestContext,
    store_product_id: UUID,
    allocations: list[Allocation],
    movement_type: InventoryMovementType,
    *,
    reference_type: str | None = None,
    reference_id: UUID | None = None,
    commit: bool = False,
) -> list[InventoryMovement]:
    """Write negative ledger movements per batch and decrement the projection.

    The availability guard runs inside the same transaction as the writes, so a
    failure leaves no half-applied consumption behind.
    """
    store_product = await load_store_product(session, context, store_product_id)
    total = sum((a.quantity for a in allocations), Decimal(0))
    balance = await _balance_for(session, store_product)
    if total > 0 and _available(balance) < total:
        raise InsufficientStock(
            f"Available stock {_available(balance)} cannot cover {total} for '{store_product.sku}'"
        )

    now = utc_now()
    movements: list[InventoryMovement] = []
    for allocation in allocations:
        movement = InventoryMovement(
            organization_id=store_product.organization_id,
            store_id=store_product.store_id,
            store_product_id=store_product.id,
            batch_id=allocation.batch_id,
            movement_type=movement_type,
            quantity=-abs(allocation.quantity),
            reference_type=reference_type,
            reference_id=reference_id,
            idempotency_key=str(uuid4()),
            occurred_at=now,
            actor_user_id=context.user_id,
        )
        session.add(movement)
        movements.append(movement)
    if total > 0:
        await _apply_to_balance(session, store_product, -total)
    if commit:
        await session.commit()
    return movements


# --- adjustments -------------------------------------------------------------


async def adjust_stock(
    session: AsyncSession,
    context: RequestContext,
    store_product_id: UUID,
    *,
    quantity: Decimal,
    reason: str,
    batch_id: UUID | None = None,
    damage: bool = False,
    request_id: str = "unknown",
    commit: bool = True,
) -> tuple[InventoryMovement, InventoryBalance]:
    """Signed correction against a batch (or the whole product when no batch).

    Owner/manager only; the router enforces this too, but services that call the
    function directly get the same guarantee.
    """
    assert_adjustment_role(context)
    if quantity == 0:
        raise ValidationError("Adjustment quantity must be non-zero")
    store_product = await load_store_product(session, context, store_product_id)
    if batch_id is not None:
        batch = await session.get(InventoryBatch, batch_id)
        if batch is None or batch.store_product_id != store_product.id:
            raise NotFound("Batch not found")

    movement_type = (
        InventoryMovementType.DAMAGE if damage else InventoryMovementType.ADJUSTMENT
    )
    now = utc_now()
    movement = InventoryMovement(
        organization_id=store_product.organization_id,
        store_id=store_product.store_id,
        store_product_id=store_product.id,
        batch_id=batch_id,
        movement_type=movement_type,
        quantity=quantity,
        reference_type="adjustment",
        reference_id=None,
        idempotency_key=str(uuid4()),
        occurred_at=now,
        actor_user_id=context.user_id,
    )
    session.add(movement)
    try:
        balance = await _apply_to_balance(session, store_product, quantity)
    except InsufficientStock:
        raise InsufficientStock(f"Adjustment rejected for '{store_product.sku}': {reason}")
    record_audit(
        session,
        context,
        action="inventory.adjusted" if not damage else "inventory.damaged",
        entity_type="inventory_movement",
        entity_id=movement.id,
        request_id=request_id,
        after=redact(
            {
                "store_product_id": str(store_product_id),
                "batch_id": str(batch_id) if batch_id else None,
                "quantity": str(quantity),
                "reason": reason,
            }
        ),
    )
    if commit:
        await session.commit()
    return movement, balance


# --- queries ------------------------------------------------------------------


async def get_stock(
    session: AsyncSession, context: RequestContext, store_product_id: UUID
) -> tuple[Decimal, Decimal]:
    store_product = await load_store_product(session, context, store_product_id)
    balance = await _balance_for(session, store_product)
    return Decimal(balance.on_hand), Decimal(balance.reserved)


async def list_stock(
    session: AsyncSession, context: RequestContext, store_id: UUID
) -> list[tuple[StoreProduct, Decimal, Decimal]]:
    store = await load_store(session, context, store_id)
    products = list(
        await session.scalars(
            select(StoreProduct).where(StoreProduct.store_id == store.id).order_by(StoreProduct.sku)
        )
    )
    result: list[tuple[StoreProduct, Decimal, Decimal]] = []
    for product in products:
        balance = await session.scalar(
            select(InventoryBalance).where(InventoryBalance.store_product_id == product.id)
        )
        on_hand = Decimal(balance.on_hand) if balance else Decimal(0)
        reserved = Decimal(balance.reserved) if balance else Decimal(0)
        result.append((product, on_hand, reserved))
    return result


async def expiring_batches(
    session: AsyncSession, context: RequestContext, store_id: UUID, *, within_days: int = 30
) -> list[tuple[StoreProduct, InventoryBatch, Decimal]]:
    if within_days < 0:
        raise ValidationError("within_days must be non-negative")
    store = await load_store(session, context, store_id)
    # Expiry is a calendar fact for the branch, so the window starts on the branch's
    # own trading day. On UTC it would shift by one for any store east of Greenwich,
    # and "expires in 30 days" would be answered against the wrong calendar.
    cutoff = business_date(store) + timedelta(days=within_days)
    batches = list(
        await session.scalars(
            select(InventoryBatch)
            .join(StoreProduct, StoreProduct.id == InventoryBatch.store_product_id)
            .where(
                InventoryBatch.store_id == store.id,
                InventoryBatch.active.is_(True),
                InventoryBatch.expiry_date.is_not(None),
                InventoryBatch.expiry_date <= cutoff,
            )
            .order_by(InventoryBatch.expiry_date)
        )
    )
    products = {
        p.id: p
        for p in await session.scalars(
            select(StoreProduct).where(StoreProduct.store_id == store.id)
        )
    }
    available_by_batch: dict[UUID, Decimal] = {}
    for product_id in {b.store_product_id for b in batches}:
        sp = products.get(product_id)
        if sp is not None:
            available_by_batch.update(await _batch_available_map(session, sp))
    today = utc_now().date()
    return [
        (products[b.store_product_id], b, available_by_batch.get(b.id, Decimal(0)))
        for b in batches
        if b.expiry_date is not None and b.expiry_date >= today
    ]


async def low_stock_products(
    session: AsyncSession, context: RequestContext, store_id: UUID
) -> list[tuple[StoreProduct, Decimal]]:
    """Products whose available stock sits below the branch minimum."""
    store = await load_store(session, context, store_id)
    products = list(
        await session.scalars(
            select(StoreProduct)
            .where(StoreProduct.store_id == store.id, StoreProduct.active.is_(True))
            .order_by(StoreProduct.sku)
        )
    )
    low: list[tuple[StoreProduct, Decimal]] = []
    for product in products:
        balance = await session.scalar(
            select(InventoryBalance).where(InventoryBalance.store_product_id == product.id)
        )
        available = _available(balance) if balance else Decimal(0)
        if available < Decimal(product.minimum_stock):
            low.append((product, available))
    return low


# --- projection rebuild -------------------------------------------------------


async def rebuild_balances_from_ledger(
    session: AsyncSession, store_id: UUID, *, commit: bool = False
) -> dict[UUID, Decimal]:
    """Recompute ``on_hand`` per store product from the movement ledger.

    The ledger is the truth; balances are disposable projections. Returns the
    rebuilt map so tests can compare it with the incremental values.
    """
    rows = await session.execute(
        select(InventoryMovement.store_product_id, InventoryMovement.quantity).where(
            InventoryMovement.store_id == store_id
        )
    )
    rebuilt = rebuild_balances([(pid, Decimal(qty)) for pid, qty in rows.all()])
    balances = list(
        await session.scalars(
            select(InventoryBalance).where(InventoryBalance.store_id == store_id)
        )
    )
    for balance in balances:
        expected = rebuilt.get(balance.store_product_id, Decimal(0))
        if Decimal(balance.on_hand) != expected:
            balance.on_hand = expected
    for missing_pid, on_hand in rebuilt.items():
        known = {b.store_product_id for b in balances}
        if missing_pid not in known:
            store_product = await session.get(StoreProduct, missing_pid)
            if store_product is not None:
                session.add(
                    InventoryBalance(
                        organization_id=store_product.organization_id,
                        store_id=store_id,
                        store_product_id=missing_pid,
                        on_hand=on_hand,
                        reserved=Decimal(0),
                    )
                )
    if commit:
        await session.commit()
    return rebuilt


def balance_view(store_product: StoreProduct, on_hand: Decimal, reserved: Decimal) -> dict[str, Decimal]:
    return {
        "on_hand": on_hand,
        "reserved": reserved,
        "available": on_hand - reserved,
    }


def to_allocation_result(result: AllocationResult) -> dict[str, object]:
    return {
        "ok": result.ok,
        "requested": result.requested,
        "allocated": result.allocated,
        "shortfall": max(result.shortfall, Decimal(0)),
        "allocations": [
            {"batch_id": a.batch_id, "quantity": a.quantity} for a in result.allocations
        ],
    }


def now_utc() -> datetime:
    return utc_now()


# --- branch transfers ---------------------------------------------------------


def assert_transfer_role(context: RequestContext) -> None:
    if context.role not in ADJUSTMENT_ROLES:
        raise Forbidden("Transfers require owner or manager")


async def load_transfer(
    session: AsyncSession, context: RequestContext, transfer_id: UUID
) -> StockTransfer:
    """A transfer of another tenant does not exist for the caller."""
    transfer = await session.get(StockTransfer, transfer_id)
    if transfer is None or transfer.organization_id != context.organization_id:
        raise NotFound("Transfer not found")
    return transfer


async def create_transfer(
    session: AsyncSession,
    context: RequestContext,
    *,
    from_store_id: UUID,
    to_store_id: UUID,
    items: list[tuple[UUID, Decimal]],
    transfer_number: str,
    request_id: str = "unknown",
) -> StockTransfer:
    """Open a draft transfer of named products from one branch to another.

    The draft holds no stock; shipping is what moves the ledger. Idempotency comes
    from the organization-scoped ``transfer_number``: a repeated number returns the
    original draft rather than a second document.
    """
    assert_transfer_role(context)
    if from_store_id == to_store_id:
        raise ValidationError("Source and destination stores must differ")
    source = await load_store(session, context, from_store_id)
    await load_store(session, context, to_store_id)

    existing = await session.scalar(
        select(StockTransfer).where(
            StockTransfer.organization_id == context.organization_id,
            StockTransfer.transfer_number == transfer_number.strip(),
        )
    )
    if existing is not None:
        return existing
    if not items:
        raise ValidationError("A transfer needs at least one line")

    transfer = StockTransfer(
        organization_id=context.organization_id,
        transfer_number=transfer_number.strip(),
        from_store_id=source.id,
        to_store_id=to_store_id,
        status=TransferStatus.DRAFT,
        created_by_user_id=context.user_id,
    )
    session.add(transfer)
    await session.flush()
    seen: set[UUID] = set()
    for store_product_id, quantity in items:
        if quantity <= 0:
            raise ValidationError("Transfer quantities must be positive")
        if store_product_id in seen:
            raise ValidationError("Duplicate product lines are not allowed")
        seen.add(store_product_id)
        product = await load_store_product(session, context, store_product_id)
        if product.store_id != source.id:
            raise NotFound("Store product not found")
        session.add(
            StockTransferItem(
                organization_id=context.organization_id,
                transfer_id=transfer.id,
                store_product_id=store_product_id,
                quantity=quantity,
            )
        )
    record_audit(
        session,
        context,
        action="inventory.transfer_created",
        entity_type="stock_transfer",
        entity_id=transfer.id,
        request_id=request_id,
        after=redact(
            {
                "transfer_number": transfer.transfer_number,
                "lines": len(items),
            }
        ),
    )
    await session.commit()
    await session.refresh(transfer)
    return transfer


async def ship_transfer(
    session: AsyncSession,
    context: RequestContext,
    transfer_id: UUID,
    *,
    request_id: str = "unknown",
) -> StockTransfer:
    """Allocate FEFO at the source branch and put the transfer in transit.

    Shipping consumes stock transactionally -- ledger movements plus the balance
    projection move together, so a failure leaves nothing half-shipped. A transfer
    can only ship once; a second call returns the unchanged document.
    """
    assert_transfer_role(context)
    transfer = await load_transfer(session, context, transfer_id)
    if transfer.status is TransferStatus.RECEIVED:
        raise Conflict("Transfer already received")
    if transfer.status is TransferStatus.IN_TRANSIT:
        return transfer
    if transfer.status is TransferStatus.CANCELLED:
        raise Conflict("Transfer cancelled")
    if context.store_id != transfer.from_store_id:
        raise Forbidden("Ship from the source branch")

    items = list(
        await session.scalars(
            select(StockTransferItem).where(StockTransferItem.transfer_id == transfer.id)
        )
    )
    now = utc_now()
    for item in items:
        result = await allocate_fefo_for_product(
            session, context, item.store_product_id, Decimal(item.quantity)
        )
        if not result.ok:
            raise InsufficientStock(
                f"Cannot ship '{item.store_product_id}': shortfall {result.shortfall}"
            )
        await consume_allocations(
            session,
            context,
            item.store_product_id,
            result.allocations,
            InventoryMovementType.TRANSFER,
            reference_type="stock_transfer",
            reference_id=transfer.id,
        )
    transfer.status = TransferStatus.IN_TRANSIT
    transfer.shipped_at = now
    record_audit(
        session,
        context,
        action="inventory.transfer_shipped",
        entity_type="stock_transfer",
        entity_id=transfer.id,
        request_id=request_id,
        after=redact({"lines": len(items)}),
    )
    await session.commit()
    await session.refresh(transfer)
    return transfer


async def receive_transfer(
    session: AsyncSession,
    context: RequestContext,
    transfer_id: UUID,
    *,
    batch_number_prefix: str | None = None,
    request_id: str = "unknown",
) -> StockTransfer:
    """Receive an in-transit transfer into the destination branch.

    Each line becomes a receipt movement against a fresh destination batch (one
    per transfer line), bumping that branch's balance projection. Receiving is
    idempotent by status: an already-received transfer returns unchanged.
    """
    assert_transfer_role(context)
    transfer = await load_transfer(session, context, transfer_id)
    if transfer.status is TransferStatus.RECEIVED:
        return transfer
    if transfer.status is not TransferStatus.IN_TRANSIT:
        raise Conflict("Transfer has not shipped")
    if context.store_id != transfer.to_store_id:
        raise Forbidden("Receive at the destination branch")

    items = list(
        await session.scalars(
            select(StockTransferItem).where(StockTransferItem.transfer_id == transfer.id)
        )
    )
    now = utc_now()
    prefix = batch_number_prefix or f"TRF-{transfer.transfer_number}"
    for item in items:
        # The destination's own listing of the same product is the target; the
        # source row belongs to another branch and must never be reused.
        destination_rows = (
            await session.execute(
                select(StoreProduct).where(
                    StoreProduct.store_id == transfer.to_store_id,
                    StoreProduct.pharmacy_product_id
                    == select(StoreProduct.pharmacy_product_id).where(
                        StoreProduct.id == item.store_product_id
                    ).scalar_subquery(),
                )
            )
        ).scalars()
        destination = destination_rows.first()
        if destination is None:
            raise ValidationError(
                f"Destination branch does not carry product '{item.store_product_id}'"
            )
        batch = InventoryBatch(
            organization_id=context.organization_id,
            store_id=destination.store_id,
            store_product_id=destination.id,
            batch_number=f"{prefix}-{item.store_product_id}"[:100],
            expiry_date=None,
            unit_cost=Decimal(0),
            received_at=now,
            active=True,
        )
        session.add(batch)
        await session.flush()
        movement = InventoryMovement(
            organization_id=context.organization_id,
            store_id=destination.store_id,
            store_product_id=destination.id,
            batch_id=batch.id,
            movement_type=InventoryMovementType.RECEIPT,
            quantity=Decimal(item.quantity),
            reference_type="stock_transfer",
            reference_id=transfer.id,
            idempotency_key=str(uuid4()),
            occurred_at=now,
            actor_user_id=context.user_id,
        )
        session.add(movement)
        await _apply_to_balance(session, destination, Decimal(item.quantity))
    transfer.status = TransferStatus.RECEIVED
    transfer.received_at = now
    record_audit(
        session,
        context,
        action="inventory.transfer_received",
        entity_type="stock_transfer",
        entity_id=transfer.id,
        request_id=request_id,
        after=redact({"lines": len(items)}),
    )
    await session.commit()
    await session.refresh(transfer)
    return transfer


async def cancel_transfer(
    session: AsyncSession,
    context: RequestContext,
    transfer_id: UUID,
    *,
    request_id: str = "unknown",
) -> StockTransfer:
    """Cancel a draft; shipped transfers must be received or investigated instead."""
    assert_transfer_role(context)
    transfer = await load_transfer(session, context, transfer_id)
    if transfer.status is TransferStatus.CANCELLED:
        return transfer
    if transfer.status is not TransferStatus.DRAFT:
        raise Conflict("Only draft transfers can be cancelled")
    transfer.status = TransferStatus.CANCELLED
    transfer.cancelled_at = utc_now()
    record_audit(
        session,
        context,
        action="inventory.transfer_cancelled",
        entity_type="stock_transfer",
        entity_id=transfer.id,
        request_id=request_id,
    )
    await session.commit()
    await session.refresh(transfer)
    return transfer


async def list_transfers(
    session: AsyncSession,
    context: RequestContext,
    *,
    status_filter: TransferStatus | None = None,
    limit: int = 25,
    offset: int = 0,
) -> tuple[list[StockTransfer], int]:
    """Transfers touching the caller's branch, newest first, with total count."""
    scope: tuple[Any, ...]
    if context.store_id is not None:
        scope = (
            StockTransfer.organization_id == context.organization_id,
            (StockTransfer.from_store_id == context.store_id)
            | (StockTransfer.to_store_id == context.store_id),
        )
    else:
        scope = (StockTransfer.organization_id == context.organization_id,)
    if status_filter is not None:
        scope = (*scope, StockTransfer.status == status_filter)
    total = int(
        await session.scalar(select(func.count()).select_from(StockTransfer).where(*scope)) or 0
    )
    rows = list(
        await session.scalars(
            select(StockTransfer)
            .where(*scope)
            .order_by(StockTransfer.created_at.desc(), StockTransfer.id)
            .limit(limit)
            .offset(offset)
        )
    )
    return rows, total

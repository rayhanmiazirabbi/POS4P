from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.context import RequestContext
from app.domains.catalog import CatalogBarcode, CatalogProduct
from app.domains.inventory import (
    Allocation,
    BatchStock,
    InventoryBalance,
    InventoryBatch,
    InventoryMovement,
    InventoryMovementType,
    StockReservation,
    StockTransfer,
    StockTransferItem,
    Stocktake,
    StocktakeItem,
    StocktakeStatus,
    TransferStatus,
    allocate_fefo,
    rebuild_balances,
)
from app.domains.products import PharmacyProduct
from app.domains.suppliers import Supplier
from app.errors import Conflict, DomainError, Forbidden, NotFound, ValidationError
from app.models import Role, StoreProduct
from app.schemas.inventory import InventoryIntakeRequest
from app.security import utc_now
from app.services.audit import record_audit, redact
from app.services.idempotency import remember, replay
from app.services.stores import business_date, load_store

#: Receiving stock is inventory work; only adjustments escalate to OWNER/MANAGER.
INVENTORY_ROLES = frozenset({Role.OWNER, Role.MANAGER, Role.CASHIER, Role.INVENTORY_STAFF})
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
    """Per-batch sellable quantity: ledger movements less active reservations.

    Reservations never write movements -- they only hold availability back --
    so an allocation that ignored them would hand the same batch units to a
    second order and drive ``available`` below zero once both hold. Expired
    holds no longer hold anything.
    """
    rows = await session.execute(
        select(InventoryMovement.batch_id, func.sum(InventoryMovement.quantity))
        .where(
            InventoryMovement.store_product_id == store_product.id,
            InventoryMovement.batch_id.is_not(None),
        )
        .group_by(InventoryMovement.batch_id)
    )
    available = {batch_id: Decimal(total or 0) for batch_id, total in rows.all()}
    now = utc_now()
    holds = await session.execute(
        select(StockReservation.batch_id, func.sum(StockReservation.quantity))
        .where(
            StockReservation.store_product_id == store_product.id,
            StockReservation.released_at.is_(None),
            or_(StockReservation.expires_at.is_(None), StockReservation.expires_at > now),
        )
        .group_by(StockReservation.batch_id)
    )
    for batch_id, held in holds.all():
        available[batch_id] = available.get(batch_id, Decimal(0)) - Decimal(held or 0)
    return available


async def release_expired_reservations(
    session: AsyncSession, *, organization_id: UUID, store_id: UUID
) -> int:
    """Release every expired hold in a branch and correct the reserved projection.

    A reservation never moved stock, so release is purely projection work: mark
    the row released and give the held quantity back to ``available`` by lowering
    ``reserved``. Rows are selected ``FOR UPDATE``, so two concurrent sweeps
    cannot both claim the same expired hold -- the loser waits, re-checks
    ``released_at IS NULL``, and skips what the winner already freed. Returns the
    number of reservations released.
    """
    now = utc_now()
    rows = (
        await session.execute(
            select(StockReservation)
            .where(
                StockReservation.organization_id == organization_id,
                StockReservation.store_id == store_id,
                StockReservation.released_at.is_(None),
                StockReservation.expires_at.is_not(None),
                StockReservation.expires_at <= now,
            )
            .with_for_update()
        )
    ).scalars().all()
    if not rows:
        return 0

    released: dict[UUID, Decimal] = {}
    for reservation in rows:
        reservation.released_at = now
        released[reservation.store_product_id] = (
            released.get(reservation.store_product_id, Decimal(0)) + Decimal(reservation.quantity)
        )
    for store_product_id, quantity in released.items():
        balance = await session.scalar(
            select(InventoryBalance)
            .where(
                InventoryBalance.store_id == store_id,
                InventoryBalance.store_product_id == store_product_id,
            )
            .with_for_update()
        )
        if balance is not None:
            balance.reserved = Decimal(balance.reserved) - quantity
    return len(rows)


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


def _intake_sku_seed(name: str) -> str:
    seed = "".join(character for character in name.upper() if character.isalnum())[:12]
    return seed or "ITEM"


async def _intake_sku(session: AsyncSession, store_id: UUID, name: str) -> str:
    seed = _intake_sku_seed(name)
    taken = set(
        await session.scalars(
            select(StoreProduct.sku).where(
                StoreProduct.store_id == store_id,
                StoreProduct.sku.like(f"{seed}%"),
            )
        )
    )
    candidate, suffix = seed, 1
    while candidate in taken:
        suffix += 1
        candidate = f"{seed}-{suffix}"
    return candidate


async def intake_inventory(
    session: AsyncSession,
    context: RequestContext,
    payload: InventoryIntakeRequest,
    *,
    idempotency_key: str,
    request_id: str,
) -> dict[str, Any]:
    """Adopt/enable/update a product and receive its stock in one transaction."""
    if context.store_id is None:
        raise ValidationError("Store context required", code="STORE_CONTEXT_REQUIRED")
    payload_dict = payload.model_dump(by_alias=True, mode="json")
    stored = await replay(session, context.organization_id, idempotency_key, payload_dict)
    if stored is not None:
        return stored

    store = await load_store(session, context, context.store_id)
    product: PharmacyProduct | None = None
    shelf: StoreProduct | None = None
    adopted = False

    if payload.store_product_id is not None:
        shelf = await load_store_product(session, context, payload.store_product_id)
        product = await session.get(PharmacyProduct, shelf.pharmacy_product_id)
    elif payload.pharmacy_product_id is not None:
        product = await session.get(PharmacyProduct, payload.pharmacy_product_id)
        if product is None or product.organization_id != context.organization_id:
            raise NotFound("Product not found")
    elif payload.catalog_product_id is not None:
        catalog = await session.get(CatalogProduct, payload.catalog_product_id)
        if catalog is None or not catalog.active:
            raise NotFound("Catalog product not found")
        product = await session.scalar(
            select(PharmacyProduct).where(
                PharmacyProduct.organization_id == context.organization_id,
                PharmacyProduct.catalog_product_id == catalog.id,
            )
        )
        if product is None:
            barcode = await session.scalar(
                select(CatalogBarcode.barcode)
                .where(CatalogBarcode.catalog_product_id == catalog.id)
                .order_by(CatalogBarcode.barcode)
            )
            if barcode is not None and await session.scalar(
                select(PharmacyProduct.id).where(
                    PharmacyProduct.organization_id == context.organization_id,
                    PharmacyProduct.barcode == barcode,
                    PharmacyProduct.active.is_(True),
                )
            ) is not None:
                barcode = None
            product = PharmacyProduct(
                organization_id=context.organization_id,
                catalog_product_id=catalog.id,
                name=catalog.name,
                barcode=barcode,
                unit=catalog.package_unit,
                active=True,
            )
            session.add(product)
            await session.flush()
            adopted = True
        else:
            product.active = True
    else:
        assert payload.custom_product is not None
        if payload.custom_product.barcode is not None and await session.scalar(
            select(PharmacyProduct.id).where(
                PharmacyProduct.organization_id == context.organization_id,
                PharmacyProduct.barcode == payload.custom_product.barcode,
                PharmacyProduct.active.is_(True),
            )
        ) is not None:
            raise Conflict(f"Barcode '{payload.custom_product.barcode}' already exists in this organization")
        product = PharmacyProduct(
            organization_id=context.organization_id,
            name=payload.custom_product.name.strip(),
            unit=payload.custom_product.unit.strip(),
            barcode=payload.custom_product.barcode,
            active=True,
        )
        session.add(product)
        await session.flush()
        adopted = True

    assert product is not None
    if shelf is None:
        shelf = await session.scalar(
            select(StoreProduct).where(
                StoreProduct.store_id == store.id,
                StoreProduct.pharmacy_product_id == product.id,
            )
        )
    if shelf is None:
        assert payload.shelf.sale_price is not None
        shelf = StoreProduct(
            organization_id=context.organization_id,
            store_id=store.id,
            pharmacy_product_id=product.id,
            sku=payload.shelf.sku or await _intake_sku(session, store.id, product.name),
            sale_price=payload.shelf.sale_price,
            minimum_stock=payload.shelf.minimum_stock or Decimal(0),
            rack=normalize_rack(payload.shelf.rack),
            active=True,
        )
        session.add(shelf)
        await session.flush()
        adopted = True
    else:
        shelf.active = True
        if payload.shelf.sku is not None:
            duplicate_sku = await session.scalar(
                select(StoreProduct.id).where(
                    StoreProduct.store_id == store.id,
                    StoreProduct.sku == payload.shelf.sku,
                    StoreProduct.id != shelf.id,
                )
            )
            if duplicate_sku is not None:
                raise Conflict(f"SKU '{payload.shelf.sku}' already exists in this store")
            shelf.sku = payload.shelf.sku
        if payload.shelf.sale_price is not None:
            shelf.sale_price = payload.shelf.sale_price
        if payload.shelf.minimum_stock is not None:
            shelf.minimum_stock = payload.shelf.minimum_stock
        if payload.shelf.rack is not None:
            shelf.rack = normalize_rack(payload.shelf.rack)

    if payload.shelf.barcode is not None:
        duplicate_barcode = await session.scalar(
            select(PharmacyProduct.id).where(
                PharmacyProduct.organization_id == context.organization_id,
                PharmacyProduct.barcode == payload.shelf.barcode,
                PharmacyProduct.id != product.id,
                PharmacyProduct.active.is_(True),
            )
        )
        if duplicate_barcode is not None:
            raise Conflict(f"Barcode '{payload.shelf.barcode}' already exists in this organization")
        product.barcode = payload.shelf.barcode
    product.active = True

    if payload.supplier_id is not None:
        supplier = await session.get(Supplier, payload.supplier_id)
        if supplier is None or supplier.organization_id != context.organization_id:
            raise NotFound("Supplier not found")

    prefix = "OPENING" if payload.source == "opening_stock" else "RECEIPT"
    batch_number = payload.batch_number or f"{prefix}-{utc_now():%Y%m%d}-{idempotency_key[-6:].upper()}"
    batch, movement, balance = await receive_batch(
        session,
        context,
        shelf.id,
        batch_number=batch_number,
        expiry_date=payload.expiry_date,
        unit_cost=payload.unit_cost if payload.unit_cost is not None else Decimal("0.00"),
        quantity=payload.quantity,
        reference_type=payload.source,
        idempotency_key=idempotency_key,
        request_id=request_id,
        commit=False,
    )
    record_audit(
        session,
        context,
        action="inventory.intake",
        entity_type="store_product",
        entity_id=shelf.id,
        request_id=request_id,
        after=redact({"source": payload.source, "adopted": adopted, "reference": payload.reference}),
    )
    response = {
        "storeProductId": str(shelf.id),
        "pharmacyProductId": str(product.id),
        "name": product.name,
        "sku": shelf.sku,
        "barcode": product.barcode,
        "salePrice": str(shelf.sale_price),
        "rack": shelf.rack,
        "unit": product.unit,
        "adopted": adopted,
        "batch": {
            "id": str(batch.id), "batchNumber": batch.batch_number,
            "expiryDate": batch.expiry_date.isoformat() if batch.expiry_date else None,
            "unitCost": str(Decimal(batch.unit_cost).quantize(Decimal("0.01"))), "receivedAt": batch.received_at.isoformat(),
        },
        "movement": {
            "id": str(movement.id), "storeProductId": str(movement.store_product_id),
            "batchId": str(movement.batch_id) if movement.batch_id else None,
            "movementType": movement.movement_type.value, "quantity": str(movement.quantity),
            "occurredAt": movement.occurred_at.isoformat(),
        },
        "balance": {
            "storeProductId": str(balance.store_product_id), "onHand": str(Decimal(balance.on_hand).quantize(Decimal("0.0001"))),
            "reserved": str(Decimal(balance.reserved).quantize(Decimal("0.0001"))),
            "available": str((Decimal(balance.on_hand) - Decimal(balance.reserved)).quantize(Decimal("0.0001"))),
        },
    }
    remember(session, context.organization_id, idempotency_key, payload_dict, response_status=201, response_body=response)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise Conflict("Intake conflicts with an existing barcode or SKU") from exc
    return response


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
        reason=reason,
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


# --- movement ledger ------------------------------------------------------------


async def list_movements(
    session: AsyncSession,
    context: RequestContext,
    *,
    store_product_id: UUID | None = None,
    movement_type: InventoryMovementType | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[tuple[InventoryMovement, StoreProduct, PharmacyProduct | None, InventoryBatch | None]], int]:
    """The append-only ledger, newest first, dressed with product and batch.

    Scoped to the caller's branch when the token carries one, so a ledger read
    cannot wander into another shop's history.
    """
    scope: list[Any] = [InventoryMovement.organization_id == context.organization_id]
    if context.store_id is not None:
        scope.append(InventoryMovement.store_id == context.store_id)
    if store_product_id is not None:
        scope.append(InventoryMovement.store_product_id == store_product_id)
    if movement_type is not None:
        scope.append(InventoryMovement.movement_type == movement_type)
    if start is not None:
        scope.append(InventoryMovement.occurred_at >= start)
    if end is not None:
        scope.append(InventoryMovement.occurred_at < end)

    total = int(
        await session.scalar(
            select(func.count()).select_from(InventoryMovement).where(*scope)
        )
        or 0
    )
    rows = list(
        await session.execute(
            select(InventoryMovement, StoreProduct, PharmacyProduct, InventoryBatch)
            .join(StoreProduct, StoreProduct.id == InventoryMovement.store_product_id)
            .outerjoin(PharmacyProduct, PharmacyProduct.id == StoreProduct.pharmacy_product_id)
            .outerjoin(InventoryBatch, InventoryBatch.id == InventoryMovement.batch_id)
            .where(*scope)
            .order_by(InventoryMovement.occurred_at.desc(), InventoryMovement.id)
            .limit(limit)
            .offset(offset)
        )
    )
    return rows, total


# --- racks ----------------------------------------------------------------------


def normalize_rack(rack: str | None) -> str | None:
    """Trim and collapse whitespace; an all-blank rack is no rack at all.

    Free-text entry forks racks on invisible differences ("Rack 1 ",
    "Rack  1"), so every write passes through here before it lands.
    """
    if rack is None:
        return None
    collapsed = " ".join(rack.split())
    return collapsed or None


async def list_racks(
    session: AsyncSession, context: RequestContext, store_id: UUID
) -> list[tuple[str, int]]:
    """Distinct normalized rack labels with their active item counts."""
    store = await load_store(session, context, store_id)
    products = list(
        await session.scalars(
            select(StoreProduct).where(
                StoreProduct.store_id == store.id, StoreProduct.active.is_(True)
            )
        )
    )
    counts: dict[str, int] = {}
    for product in products:
        label = normalize_rack(product.rack)
        if label is not None:
            counts[label] = counts.get(label, 0) + 1
    return sorted(counts.items())


async def rename_rack(
    session: AsyncSession,
    context: RequestContext,
    store_id: UUID,
    *,
    from_rack: str,
    to_rack: str,
    request_id: str = "unknown",
) -> int:
    """Move every item on one rack label to another; returns items moved.

    Normalization on both ends means renaming "rack 1" into "Rack 1" is the
    same operation as fixing a typo: match what is stored, write what is asked.
    """
    assert_adjustment_role(context)
    store = await load_store(session, context, store_id)
    source = normalize_rack(from_rack)
    target = normalize_rack(to_rack)
    if source is None or target is None:
        raise ValidationError("Rack names cannot be blank")
    if source == target:
        return 0
    products = list(
        await session.scalars(
            select(StoreProduct).where(StoreProduct.store_id == store.id)
        )
    )
    moved = 0
    for product in products:
        if normalize_rack(product.rack) == source:
            product.rack = target
            moved += 1
    record_audit(
        session,
        context,
        action="inventory.rack_renamed",
        entity_type="store_product",
        entity_id=store.id,
        request_id=request_id,
        after=redact({"from": source, "to": target, "moved": moved}),
    )
    await session.commit()
    return moved


# --- reorder suggestions ----------------------------------------------------------


async def reorder_suggestions(
    session: AsyncSession, context: RequestContext, store_id: UUID
) -> list[tuple[StoreProduct, PharmacyProduct | None, Decimal, Decimal]]:
    """Below-minimum products with a suggested order quantity.

    The suggestion refills to twice the minimum -- enough to clear the minimum
    again without an immediate second order -- and is never below the minimum
    itself, so a branch with a very low minimum still orders a useful amount.
    """
    store = await load_store(session, context, store_id)
    low = await low_stock_products(session, context, store.id)
    product_ids = [product.id for product, _available in low]
    pharmacy_products = {
        p.id: p
        for p in await session.scalars(
            select(PharmacyProduct).where(PharmacyProduct.id.in_(product_ids))
        )
    } if product_ids else {}
    result: list[tuple[StoreProduct, PharmacyProduct | None, Decimal, Decimal]] = []
    for product, available in low:
        minimum = Decimal(product.minimum_stock)
        suggested = max(minimum * 2 - available, minimum)
        result.append((product, pharmacy_products.get(product.pharmacy_product_id), available, suggested))
    return result


# --- stocktakes -----------------------------------------------------------------


async def create_stocktake(
    session: AsyncSession,
    context: RequestContext,
    *,
    note: str | None = None,
    request_id: str = "unknown",
) -> Stocktake:
    """Open a count session for the caller's branch."""
    assert_adjustment_role(context)
    if context.store_id is None:
        raise ValidationError("A count session needs the caller's branch", code="STORE_CONTEXT_REQUIRED")
    stocktake = Stocktake(
        organization_id=context.organization_id,
        store_id=context.store_id,
        note=note,
        status=StocktakeStatus.DRAFT,
        created_by_user_id=context.user_id,
    )
    session.add(stocktake)
    record_audit(
        session,
        context,
        action="inventory.stocktake_opened",
        entity_type="stocktake",
        entity_id=stocktake.id,
        request_id=request_id,
        after=redact({"note": note}),
    )
    await session.commit()
    await session.refresh(stocktake)
    return stocktake


async def load_stocktake(
    session: AsyncSession, context: RequestContext, stocktake_id: UUID
) -> Stocktake:
    stocktake = await session.get(Stocktake, stocktake_id)
    if (
        stocktake is None
        or stocktake.organization_id != context.organization_id
        or (context.store_id is not None and stocktake.store_id != context.store_id)
    ):
        raise NotFound("Stocktake not found")
    return stocktake


async def _stocktake_lines(
    session: AsyncSession, context: RequestContext, stocktake: Stocktake
) -> list[tuple[StocktakeItem, StoreProduct, PharmacyProduct | None]]:
    """Counted lines joined to their products, with system on-hand per line."""
    rows = list(
        await session.scalars(
            select(StocktakeItem).where(StocktakeItem.stocktake_id == stocktake.id)
        )
    )
    lines: list[tuple[StocktakeItem, StoreProduct, PharmacyProduct | None]] = []
    for item in rows:
        product = await session.get(StoreProduct, item.store_product_id)
        if product is None or product.store_id != stocktake.store_id:
            continue
        pharmacy_product = await session.get(PharmacyProduct, product.pharmacy_product_id)
        lines.append((item, product, pharmacy_product))
    return lines


async def upsert_stocktake_line(
    session: AsyncSession,
    context: RequestContext,
    stocktake_id: UUID,
    *,
    store_product_id: UUID,
    counted_quantity: Decimal,
) -> StocktakeItem:
    """Record one counted quantity; a second count of the same line replaces it."""
    assert_adjustment_role(context)
    stocktake = await load_stocktake(session, context, stocktake_id)
    if stocktake.status != StocktakeStatus.DRAFT:
        raise Conflict("Only a draft count session can be edited")
    store_product = await load_store_product(session, context, store_product_id)
    if store_product.store_id != stocktake.store_id:
        raise NotFound("Store product not found")
    item = await session.scalar(
        select(StocktakeItem).where(
            StocktakeItem.stocktake_id == stocktake.id,
            StocktakeItem.store_product_id == store_product.id,
        )
    )
    if item is None:
        item = StocktakeItem(
            organization_id=context.organization_id,
            stocktake_id=stocktake.id,
            store_product_id=store_product.id,
            counted_quantity=counted_quantity,
        )
        session.add(item)
    else:
        item.counted_quantity = counted_quantity
    await session.commit()
    await session.refresh(item)
    return item


async def stocktake_view(
    session: AsyncSession, context: RequestContext, stocktake_id: UUID
) -> tuple[Stocktake, list[tuple[StocktakeItem, StoreProduct, PharmacyProduct | None, Decimal]]]:
    """The session with each line's counted vs system quantity and variance."""
    stocktake = await load_stocktake(session, context, stocktake_id)
    lines = await _stocktake_lines(session, context, stocktake)
    dressed: list[tuple[StocktakeItem, StoreProduct, PharmacyProduct | None, Decimal]] = []
    for item, product, pharmacy_product in lines:
        balance = await session.scalar(
            select(InventoryBalance).where(InventoryBalance.store_product_id == product.id)
        )
        system = Decimal(balance.on_hand) if balance else Decimal(0)
        dressed.append((item, product, pharmacy_product, system - item.counted_quantity))
    return stocktake, dressed


async def finalize_stocktake(
    session: AsyncSession,
    context: RequestContext,
    stocktake_id: UUID,
    *,
    request_id: str = "unknown",
) -> tuple[Stocktake, int, int]:
    """Book every variance as an adjustment and close the session.

    Each non-zero delta becomes its own ``adjustment`` movement -- batchless,
    like every manual correction -- carrying the stocktake id as its reference
    so the ledger can show which count a correction came from. Zero-variance
    lines are recorded as counted but move nothing.
    """
    assert_adjustment_role(context)
    stocktake = await load_stocktake(session, context, stocktake_id)
    if stocktake.status != StocktakeStatus.DRAFT:
        raise Conflict("Stocktake already finalized")
    lines = await _stocktake_lines(session, context, stocktake)
    corrected = unchanged = 0
    note = stocktake.note or "stocktake"
    for item, product, _pharmacy_product in lines:
        balance = await session.scalar(
            select(InventoryBalance).where(InventoryBalance.store_product_id == product.id)
        )
        system = Decimal(balance.on_hand) if balance else Decimal(0)
        delta = item.counted_quantity - system
        if delta == 0:
            unchanged += 1
            continue
        movement = InventoryMovement(
            organization_id=product.organization_id,
            store_id=product.store_id,
            store_product_id=product.id,
            batch_id=None,
            movement_type=InventoryMovementType.ADJUSTMENT,
            quantity=delta,
            reference_type="stocktake",
            reference_id=stocktake.id,
            idempotency_key=str(uuid4()),
            reason=f"Count correction ({note}): system {system}, counted {item.counted_quantity}",
            occurred_at=utc_now(),
            actor_user_id=context.user_id,
        )
        session.add(movement)
        await _apply_to_balance(session, product, delta)
        corrected += 1
    stocktake.status = StocktakeStatus.COMPLETED
    stocktake.completed_at = utc_now()
    record_audit(
        session,
        context,
        action="inventory.stocktake_finalized",
        entity_type="stocktake",
        entity_id=stocktake.id,
        request_id=request_id,
        after=redact({"corrected": corrected, "unchanged": unchanged}),
    )
    await session.commit()
    await session.refresh(stocktake)
    return stocktake, corrected, unchanged


async def list_stocktakes(
    session: AsyncSession,
    context: RequestContext,
    *,
    limit: int = 25,
    offset: int = 0,
) -> tuple[list[Stocktake], int]:
    """Count sessions for the caller's branch, newest first."""
    scope: list[Any] = [Stocktake.organization_id == context.organization_id]
    if context.store_id is not None:
        scope.append(Stocktake.store_id == context.store_id)
    total = int(
        await session.scalar(select(func.count()).select_from(Stocktake).where(*scope)) or 0
    )
    rows = list(
        await session.scalars(
            select(Stocktake)
            .where(*scope)
            .order_by(Stocktake.created_at.desc(), Stocktake.id)
            .limit(limit)
            .offset(offset)
        )
    )
    return rows, total

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from uuid import UUID

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import (
    AppendOnlyMixin,
    Base,
    OrganizationScopedMixin,
    StoreScopedMixin,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
    enum_column,
    money_column,
    quantity_column,
)


class InventoryMovementType(str, Enum):
    RECEIPT = "receipt"
    SALE = "sale"
    RETURN = "return"
    ADJUSTMENT = "adjustment"
    DAMAGE = "damage"
    TRANSFER = "transfer"


class InventoryBatch(StoreScopedMixin, UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "inventory_batches"
    __table_args__ = (Index("ix_inventory_batches_fefo", "store_id", "store_product_id", "expiry_date"),)

    store_product_id: Mapped[UUID] = mapped_column(ForeignKey("store_products.id"), nullable=False)
    batch_number: Mapped[str] = mapped_column(String(100), nullable=False)
    expiry_date: Mapped[date | None] = mapped_column(Date)
    unit_cost: Mapped[Decimal] = mapped_column(money_column(), nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class InventoryMovement(AppendOnlyMixin, StoreScopedMixin, UUIDPrimaryKeyMixin, Base):
    __tablename__ = "inventory_movements"
    __table_args__ = (Index("ix_inventory_movement_product_time", "store_id", "store_product_id", "occurred_at"),)

    store_product_id: Mapped[UUID] = mapped_column(ForeignKey("store_products.id"), nullable=False)
    batch_id: Mapped[UUID | None] = mapped_column(ForeignKey("inventory_batches.id"))
    movement_type: Mapped[InventoryMovementType] = mapped_column(nullable=False)
    quantity: Mapped[Decimal] = mapped_column(quantity_column(), nullable=False)
    reference_type: Mapped[str | None] = mapped_column(String(80))
    reference_id: Mapped[UUID | None] = mapped_column()
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    actor_user_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"))


class InventoryBalance(StoreScopedMixin, UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "inventory_balances"
    __table_args__ = (UniqueConstraint("store_id", "store_product_id"),)

    store_product_id: Mapped[UUID] = mapped_column(ForeignKey("store_products.id"), nullable=False)
    on_hand: Mapped[Decimal] = mapped_column(quantity_column(), default=0, nullable=False)
    reserved: Mapped[Decimal] = mapped_column(quantity_column(), default=0, nullable=False)


class StockReservation(StoreScopedMixin, UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "stock_reservations"
    __table_args__ = (UniqueConstraint("store_id", "reference_type", "reference_id", "batch_id"),)

    store_product_id: Mapped[UUID] = mapped_column(ForeignKey("store_products.id"), nullable=False)
    batch_id: Mapped[UUID] = mapped_column(ForeignKey("inventory_batches.id"), nullable=False)
    reference_type: Mapped[str] = mapped_column(String(80), nullable=False)
    reference_id: Mapped[UUID] = mapped_column(nullable=False)
    quantity: Mapped[Decimal] = mapped_column(quantity_column(), nullable=False)
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class TransferStatus(str, Enum):
    DRAFT = "draft"
    IN_TRANSIT = "in_transit"
    RECEIVED = "received"
    CANCELLED = "cancelled"


class StockTransfer(OrganizationScopedMixin, UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "stock_transfers"
    __table_args__ = (
        UniqueConstraint("organization_id", "transfer_number"),
        Index("ix_stock_transfers_org_status", "organization_id", "status"),
    )

    transfer_number: Mapped[str] = mapped_column(String(60), nullable=False)
    from_store_id: Mapped[UUID] = mapped_column(ForeignKey("stores.id"), nullable=False)
    to_store_id: Mapped[UUID] = mapped_column(ForeignKey("stores.id"), nullable=False)
    status: Mapped[TransferStatus] = mapped_column(
        enum_column(TransferStatus), nullable=False, default=TransferStatus.DRAFT
    )
    shipped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by_user_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"))


class StockTransferItem(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "stock_transfer_items"
    __table_args__ = (UniqueConstraint("transfer_id", "store_product_id"),)

    organization_id: Mapped[UUID] = mapped_column(ForeignKey("organizations.id"), nullable=False)
    transfer_id: Mapped[UUID] = mapped_column(ForeignKey("stock_transfers.id"), nullable=False)
    store_product_id: Mapped[UUID] = mapped_column(ForeignKey("store_products.id"), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(quantity_column(), nullable=False)


@dataclass(frozen=True)
class BatchStock:
    batch_id: UUID
    available: Decimal
    expiry_date: date | None
    received_at: datetime


@dataclass(frozen=True)
class Allocation:
    batch_id: UUID
    quantity: Decimal


def allocate_fefo(batches: list[BatchStock], requested: Decimal, as_of: date) -> tuple[list[Allocation], Decimal]:
    """Allocate unexpired stock by expiry and UUID, returning an explicit shortfall."""
    if requested < 0:
        raise ValueError("requested quantity cannot be negative")
    eligible = sorted(
        (batch for batch in batches if batch.available > 0 and (batch.expiry_date is None or batch.expiry_date >= as_of)),
        key=lambda batch: (batch.expiry_date is None, batch.expiry_date or date.max, batch.received_at, batch.batch_id),
    )
    remaining = requested
    allocations: list[Allocation] = []
    for batch in eligible:
        if remaining <= 0:
            break
        quantity = min(batch.available, remaining)
        allocations.append(Allocation(batch.batch_id, quantity))
        remaining -= quantity
    return allocations, remaining


def rebuild_balances(movements: list[tuple[UUID, Decimal]]) -> dict[UUID, Decimal]:
    """Rebuild on-hand totals from signed ledger quantities."""
    totals: dict[UUID, Decimal] = {}
    for product_id, quantity in movements:
        totals[product_id] = totals.get(product_id, Decimal(0)) + quantity
    return totals

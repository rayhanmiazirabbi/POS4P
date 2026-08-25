from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import Enum
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import (
    Base,
    StoreScopedMixin,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
    money_column,
    quantity_column,
)


class SaleStatus(str, Enum):
    COMPLETED = "completed"
    VOIDED = "voided"
    REFUNDED = "refunded"


class SaleChannel(str, Enum):
    POS = "pos"
    ONLINE = "online"


class Sale(StoreScopedMixin, UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "sales"
    __table_args__ = (UniqueConstraint("organization_id", "idempotency_key"),)

    customer_id: Mapped[UUID | None] = mapped_column(ForeignKey("customers.id"))
    order_id: Mapped[UUID | None] = mapped_column()
    channel: Mapped[SaleChannel] = mapped_column(default=SaleChannel.POS, nullable=False)
    status: Mapped[SaleStatus] = mapped_column(default=SaleStatus.COMPLETED, nullable=False)
    subtotal: Mapped[Decimal] = mapped_column(money_column(), nullable=False)
    discount: Mapped[Decimal] = mapped_column(money_column(), default=0, nullable=False)
    total: Mapped[Decimal] = mapped_column(money_column(), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    receipt_number: Mapped[str | None] = mapped_column(String(80))
    void_reason: Mapped[str | None] = mapped_column(Text)


class SaleItem(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "sale_items"

    sale_id: Mapped[UUID] = mapped_column(ForeignKey("sales.id"), nullable=False)
    store_product_id: Mapped[UUID] = mapped_column(ForeignKey("store_products.id"), nullable=False)
    product_name: Mapped[str] = mapped_column(String(240), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(quantity_column(), nullable=False)
    unit_price: Mapped[Decimal] = mapped_column(money_column(), nullable=False)
    line_total: Mapped[Decimal] = mapped_column(money_column(), nullable=False)


class SaleItemBatchAllocation(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "sale_item_batch_allocations"

    sale_item_id: Mapped[UUID] = mapped_column(ForeignKey("sale_items.id"), nullable=False)
    batch_id: Mapped[UUID] = mapped_column(ForeignKey("inventory_batches.id"), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(quantity_column(), nullable=False)


class SaleReturn(UUIDPrimaryKeyMixin, StoreScopedMixin, Base):
    __tablename__ = "sale_returns"
    __table_args__ = (UniqueConstraint("organization_id", "idempotency_key"),)

    sale_id: Mapped[UUID] = mapped_column(ForeignKey("sales.id"), nullable=False)
    reason: Mapped[str] = mapped_column(String(240), nullable=False)
    total: Mapped[Decimal] = mapped_column(money_column(), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

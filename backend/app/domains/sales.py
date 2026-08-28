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
    line_discount: Mapped[Decimal] = mapped_column(money_column(), default=0, nullable=False)
    global_discount: Mapped[Decimal] = mapped_column(money_column(), default=0, nullable=False)
    delivery_charge: Mapped[Decimal] = mapped_column(money_column(), default=0, nullable=False)
    other_fee_label: Mapped[str | None] = mapped_column(String(120))
    other_fee: Mapped[Decimal] = mapped_column(money_column(), default=0, nullable=False)
    advance_applied: Mapped[Decimal] = mapped_column(money_column(), default=0, nullable=False)
    advance_reference: Mapped[str | None] = mapped_column(String(160))
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
    discount_mode: Mapped[str | None] = mapped_column(String(20))
    discount_value: Mapped[Decimal] = mapped_column(money_column(), default=0, nullable=False)
    discount_amount: Mapped[Decimal] = mapped_column(money_column(), default=0, nullable=False)
    line_total: Mapped[Decimal] = mapped_column(money_column(), nullable=False)


class DiscountApproval(UUIDPrimaryKeyMixin, StoreScopedMixin, Base):
    __tablename__ = "discount_approvals"
    __table_args__ = (UniqueConstraint("token_hash"),)

    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    approved_by_user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


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
    advance_restored: Mapped[Decimal] = mapped_column(money_column(), default=0, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

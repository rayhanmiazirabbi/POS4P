from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from uuid import UUID

from sqlalchemy import Date, DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import (
    Base,
    StoreScopedMixin,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
    money_column,
    quantity_column,
)


class PurchaseStatus(str, Enum):
    DRAFT = "draft"
    CONFIRMED = "confirmed"
    CANCELLED = "cancelled"
    RETURNED = "returned"


class Purchase(StoreScopedMixin, UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "purchases"
    __table_args__ = (
        UniqueConstraint("organization_id", "idempotency_key"),
        UniqueConstraint("store_id", "receipt_number", name="uq_purchases_store_receipt_number"),
    )

    supplier_id: Mapped[UUID] = mapped_column(ForeignKey("suppliers.id"), nullable=False)
    status: Mapped[PurchaseStatus] = mapped_column(default=PurchaseStatus.DRAFT, nullable=False)
    invoice_number: Mapped[str | None] = mapped_column(String(100))
    receipt_number: Mapped[str | None] = mapped_column(String(80))
    total_amount: Mapped[Decimal] = mapped_column(money_column(), default=0, nullable=False)
    purchased_at: Mapped[date] = mapped_column(Date, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    note: Mapped[str | None] = mapped_column(Text)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PurchaseItem(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "purchase_items"

    purchase_id: Mapped[UUID] = mapped_column(ForeignKey("purchases.id"), nullable=False)
    store_product_id: Mapped[UUID] = mapped_column(ForeignKey("store_products.id"), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(quantity_column(), nullable=False)
    unit_cost: Mapped[Decimal] = mapped_column(money_column(), nullable=False)
    line_total: Mapped[Decimal] = mapped_column(money_column(), nullable=False)
    batch_number: Mapped[str] = mapped_column(String(100), nullable=False)
    expiry_date: Mapped[date | None] = mapped_column(Date)

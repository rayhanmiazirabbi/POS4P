from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import Enum
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Index, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import AppendOnlyMixin, Base, StoreScopedMixin, UUIDPrimaryKeyMixin


class PaymentMethod(str, Enum):
    CASH = "cash"
    BKASH = "bkash"
    NAGAD = "nagad"
    DUE = "due"


class PaymentStatus(str, Enum):
    PENDING = "pending"
    CAPTURED = "captured"
    FAILED = "failed"
    REFUNDED = "refunded"


class Payment(StoreScopedMixin, UUIDPrimaryKeyMixin, Base):
    __tablename__ = "payments"
    __table_args__ = (UniqueConstraint("organization_id", "idempotency_key"), Index("ix_payments_reference", "reference_type", "reference_id"))

    reference_type: Mapped[str] = mapped_column(String(40), nullable=False)
    reference_id: Mapped[UUID] = mapped_column(nullable=False)
    customer_id: Mapped[UUID | None] = mapped_column(ForeignKey("customers.id"))
    method: Mapped[PaymentMethod] = mapped_column(nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    received_amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    status: Mapped[PaymentStatus] = mapped_column(default=PaymentStatus.CAPTURED, nullable=False)
    provider_reference: Mapped[str | None] = mapped_column(String(160))
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class PaymentRefund(AppendOnlyMixin, StoreScopedMixin, UUIDPrimaryKeyMixin, Base):
    __tablename__ = "payment_refunds"

    payment_id: Mapped[UUID] = mapped_column(ForeignKey("payments.id"), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    provider_reference: Mapped[str | None] = mapped_column(String(160))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import Enum
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Index, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import AppendOnlyMixin, Base, OrganizationScopedMixin, StoreScopedMixin, TimestampMixin, UUIDPrimaryKeyMixin


class SupplierStatus(str, Enum):
    ACTIVE = "active"
    INACTIVE = "inactive"


class Supplier(OrganizationScopedMixin, UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "suppliers"
    __table_args__ = (UniqueConstraint("organization_id", "name"),)

    name: Mapped[str] = mapped_column(String(180), nullable=False)
    phone: Mapped[str | None] = mapped_column(String(32))
    address: Mapped[str | None] = mapped_column(Text)
    status: Mapped[SupplierStatus] = mapped_column(default=SupplierStatus.ACTIVE, nullable=False)


class SupplierProduct(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "supplier_products"
    __table_args__ = (UniqueConstraint("supplier_id", "pharmacy_product_id"),)

    supplier_id: Mapped[UUID] = mapped_column(ForeignKey("suppliers.id"), nullable=False)
    pharmacy_product_id: Mapped[UUID] = mapped_column(ForeignKey("pharmacy_products.id"), nullable=False)
    supplier_sku: Mapped[str | None] = mapped_column(String(80))
    preferred: Mapped[bool] = mapped_column(default=False, nullable=False)


class SupplierLedgerEntry(AppendOnlyMixin, StoreScopedMixin, UUIDPrimaryKeyMixin, Base):
    __tablename__ = "supplier_ledger_entries"
    __table_args__ = (Index("ix_supplier_ledger_scope_supplier", "organization_id", "supplier_id", "created_at"),)

    supplier_id: Mapped[UUID] = mapped_column(ForeignKey("suppliers.id"), nullable=False)
    entry_type: Mapped[str] = mapped_column(String(40), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    reference_type: Mapped[str | None] = mapped_column(String(80))
    reference_id: Mapped[UUID | None] = mapped_column()
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

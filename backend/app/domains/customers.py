from __future__ import annotations

from decimal import Decimal
from uuid import UUID

from sqlalchemy import JSON, Boolean, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import (
    Base,
    OrganizationScopedMixin,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
    money_column,
)


class Customer(OrganizationScopedMixin, UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "customers"
    __table_args__ = (UniqueConstraint("organization_id", "normalized_phone"), Index("ix_customers_org_name", "organization_id", "name"))

    name: Mapped[str] = mapped_column(String(160), nullable=False)
    normalized_phone: Mapped[str | None] = mapped_column(String(32))
    email: Mapped[str | None] = mapped_column(String(254))
    due_balance: Mapped[Decimal] = mapped_column(money_column(), default=0, nullable=False)
    advance_balance: Mapped[Decimal] = mapped_column(money_column(), default=0, nullable=False)
    preferences: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class CustomerAddress(OrganizationScopedMixin, UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "customer_addresses"

    customer_id: Mapped[UUID] = mapped_column(ForeignKey("customers.id"), nullable=False)
    label: Mapped[str] = mapped_column(String(40), nullable=False)
    address_line: Mapped[str] = mapped_column(String(300), nullable=False)
    city: Mapped[str | None] = mapped_column(String(100))
    postal_code: Mapped[str | None] = mapped_column(String(20))
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

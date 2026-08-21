from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import (
    AppendOnlyMixin,
    Base,
    OrganizationScopedMixin,
    StoreScopedMixin,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
)


class PharmacyProduct(OrganizationScopedMixin, UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "pharmacy_products"
    __table_args__ = (
        UniqueConstraint("organization_id", "barcode"),
        Index("ix_pharmacy_products_org_name", "organization_id", "name"),
    )

    catalog_product_id: Mapped[UUID | None] = mapped_column(ForeignKey("catalog_products.id"))
    name: Mapped[str] = mapped_column(String(240), nullable=False)
    barcode: Mapped[str | None] = mapped_column(String(64))
    unit: Mapped[str] = mapped_column(String(40), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class StoreProduct(StoreScopedMixin, UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "store_products"
    __table_args__ = (
        UniqueConstraint("store_id", "pharmacy_product_id"),
        UniqueConstraint("store_id", "sku"),
        Index("ix_store_products_scope", "organization_id", "store_id"),
    )

    pharmacy_product_id: Mapped[UUID] = mapped_column(ForeignKey("pharmacy_products.id"), nullable=False)
    sku: Mapped[str] = mapped_column(String(64), nullable=False)
    sale_price: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    minimum_stock: Mapped[Decimal] = mapped_column(Numeric(18, 4), default=0, nullable=False)
    rack: Mapped[str | None] = mapped_column(String(80))
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class StoreProductPrice(AppendOnlyMixin, StoreScopedMixin, UUIDPrimaryKeyMixin, Base):
    __tablename__ = "store_product_prices"
    __table_args__ = (Index("ix_product_price_history", "store_product_id", "effective_at"),)

    store_product_id: Mapped[UUID] = mapped_column(ForeignKey("store_products.id"), nullable=False)
    price: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    effective_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    actor_user_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"))

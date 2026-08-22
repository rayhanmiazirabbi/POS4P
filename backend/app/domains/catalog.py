from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import (
    AppendOnlyMixin,
    Base,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
    quantity_column,
)


class Manufacturer(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "manufacturers"

    name: Mapped[str] = mapped_column(String(160), unique=True, nullable=False)
    country_code: Mapped[str | None] = mapped_column(String(2))
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class ActiveIngredient(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "active_ingredients"

    name: Mapped[str] = mapped_column(String(160), unique=True, nullable=False)


class DosageForm(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "dosage_forms"

    name: Mapped[str] = mapped_column(String(80), unique=True, nullable=False)


class CatalogProduct(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "catalog_products"
    __table_args__ = (Index("ix_catalog_product_name", "name"),)

    manufacturer_id: Mapped[UUID | None] = mapped_column(ForeignKey("manufacturers.id"))
    dosage_form_id: Mapped[UUID | None] = mapped_column(ForeignKey("dosage_forms.id"))
    name: Mapped[str] = mapped_column(String(240), nullable=False)
    strength: Mapped[str | None] = mapped_column(String(100))
    package_size: Mapped[Decimal] = mapped_column(quantity_column(), default=1, nullable=False)
    package_unit: Mapped[str] = mapped_column(String(40), nullable=False)
    prescription_required: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    country_code: Mapped[str] = mapped_column(String(2), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class CatalogProductIngredient(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "catalog_product_ingredients"
    __table_args__ = (UniqueConstraint("catalog_product_id", "active_ingredient_id"),)

    catalog_product_id: Mapped[UUID] = mapped_column(ForeignKey("catalog_products.id"), nullable=False)
    active_ingredient_id: Mapped[UUID] = mapped_column(ForeignKey("active_ingredients.id"), nullable=False)
    strength: Mapped[Decimal | None] = mapped_column(quantity_column())
    unit: Mapped[str | None] = mapped_column(String(30))


class CatalogBarcode(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "catalog_barcodes"

    catalog_product_id: Mapped[UUID] = mapped_column(ForeignKey("catalog_products.id"), nullable=False)
    barcode: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)


class CatalogAlias(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "catalog_aliases"
    __table_args__ = (UniqueConstraint("catalog_product_id", "alias"),)

    catalog_product_id: Mapped[UUID] = mapped_column(ForeignKey("catalog_products.id"), nullable=False)
    alias: Mapped[str] = mapped_column(String(240), nullable=False)


class CatalogRevision(AppendOnlyMixin, UUIDPrimaryKeyMixin, Base):
    __tablename__ = "catalog_revisions"
    __table_args__ = (UniqueConstraint("catalog_product_id", "revision"),)

    catalog_product_id: Mapped[UUID] = mapped_column(ForeignKey("catalog_products.id"), nullable=False)
    revision: Mapped[int] = mapped_column(nullable=False)
    data: Mapped[dict] = mapped_column(JSON, nullable=False)
    changed_by_user_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

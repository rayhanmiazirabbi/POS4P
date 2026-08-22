from __future__ import annotations

from decimal import Decimal
from uuid import UUID

from sqlalchemy import Boolean, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import (
    Base,
    StoreScopedMixin,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
    money_column,
)


class Storefront(StoreScopedMixin, UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "storefronts"
    __table_args__ = (UniqueConstraint("organization_id", "slug"), UniqueConstraint("store_id", "slug"))

    slug: Mapped[str] = mapped_column(String(100), nullable=False)
    display_name: Mapped[str] = mapped_column(String(160), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    custom_domain: Mapped[str | None] = mapped_column(String(255), unique=True)


class EcommerceProductSetting(StoreScopedMixin, UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "ecommerce_product_settings"
    __table_args__ = (UniqueConstraint("store_id", "store_product_id"),)

    store_product_id: Mapped[UUID] = mapped_column(ForeignKey("store_products.id"), nullable=False)
    online_name: Mapped[str | None] = mapped_column(String(240))
    description: Mapped[str | None] = mapped_column(Text)
    online_price: Mapped[Decimal | None] = mapped_column(money_column())
    listed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    pickup_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    delivery_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

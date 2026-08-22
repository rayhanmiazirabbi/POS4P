from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import JSON, Date, DateTime, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import (
    Base,
    StoreScopedMixin,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
    money_column,
)


class DailyStoreMetric(StoreScopedMixin, UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "daily_store_metrics"
    __table_args__ = (UniqueConstraint("store_id", "metric_date"), Index("ix_daily_metrics_scope_date", "organization_id", "metric_date"))

    metric_date: Mapped[date] = mapped_column(Date, nullable=False)
    sales_total: Mapped[Decimal] = mapped_column(money_column(), default=0, nullable=False)
    refund_total: Mapped[Decimal] = mapped_column(money_column(), default=0, nullable=False)
    cost_total: Mapped[Decimal] = mapped_column(money_column(), default=0, nullable=False)
    payment_breakdown: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    rebuilt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class StoreExpense(StoreScopedMixin, UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "store_expenses"

    category: Mapped[str] = mapped_column(String(100), nullable=False)
    amount: Mapped[Decimal] = mapped_column(money_column(), nullable=False)
    expense_date: Mapped[date] = mapped_column(Date, nullable=False)
    note: Mapped[str | None] = mapped_column(String(500))
    created_by_user_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"))

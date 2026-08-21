from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import Enum
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, JSON, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, OrganizationScopedMixin, TimestampMixin, UUIDPrimaryKeyMixin


class SubscriptionStatus(str, Enum):
    TRIAL = "trial"
    ACTIVE = "active"
    PAST_DUE = "past_due"
    CANCELLED = "cancelled"


class BillingPlan(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "billing_plans"

    code: Mapped[str] = mapped_column(String(80), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    monthly_amount: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    entitlements: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    active: Mapped[bool] = mapped_column(default=True, nullable=False)


class OrganizationSubscription(OrganizationScopedMixin, UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "organization_subscriptions"

    plan_id: Mapped[UUID] = mapped_column(ForeignKey("billing_plans.id"), nullable=False)
    status: Mapped[SubscriptionStatus] = mapped_column(default=SubscriptionStatus.TRIAL, nullable=False)
    current_period_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    grace_period_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    provider_subscription_id: Mapped[str | None] = mapped_column(String(160), unique=True)


class BillingInvoice(OrganizationScopedMixin, UUIDPrimaryKeyMixin, Base):
    __tablename__ = "billing_invoices"
    __table_args__ = (UniqueConstraint("organization_id", "invoice_number"),)

    subscription_id: Mapped[UUID] = mapped_column(ForeignKey("organization_subscriptions.id"), nullable=False)
    invoice_number: Mapped[str] = mapped_column(String(80), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(40), nullable=False)

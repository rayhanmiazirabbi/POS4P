from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Integer, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base import AppendOnlyMixin, Base, OrganizationScopedMixin, TimestampMixin, UUIDPrimaryKeyMixin


class LoyaltyTransactionType(str, Enum):
    EARN = "earn"
    REDEEM = "redeem"
    REFUND = "refund"
    BONUS = "bonus"
    ADJUST = "adjust"
    EXPIRE = "expire"


class LoyaltyAccount(OrganizationScopedMixin, UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "loyalty_accounts"
    __table_args__ = (UniqueConstraint("organization_id", "customer_id"),)

    customer_id: Mapped[UUID] = mapped_column(ForeignKey("customers.id"), nullable=False)
    balance: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    active: Mapped[bool] = mapped_column(default=True, nullable=False)


class LoyaltyTransaction(AppendOnlyMixin, OrganizationScopedMixin, UUIDPrimaryKeyMixin, Base):
    __tablename__ = "loyalty_transactions"
    __table_args__ = (UniqueConstraint("organization_id", "idempotency_key"),)

    account_id: Mapped[UUID] = mapped_column(ForeignKey("loyalty_accounts.id"), nullable=False)
    transaction_type: Mapped[LoyaltyTransactionType] = mapped_column(nullable=False)
    points: Mapped[int] = mapped_column(Integer, nullable=False)
    source_type: Mapped[str] = mapped_column(String(80), nullable=False)
    source_id: Mapped[UUID] = mapped_column(nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


def rebuild_loyalty_balance(transactions: list[int]) -> int:
    balance = sum(transactions)
    if balance < 0:
        raise ValueError("loyalty ledger would produce a negative balance")
    return balance


@dataclass(frozen=True)
class LoyaltyDelta:
    points: int
    balance: int


def apply_loyalty_delta(balance: int, points: int) -> LoyaltyDelta:
    new_balance = balance + points
    if new_balance < 0:
        raise ValueError("insufficient loyalty points")
    return LoyaltyDelta(points, new_balance)

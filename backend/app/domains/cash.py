from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import Enum
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Index, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import (
    Base,
    StoreScopedMixin,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
    enum_column,
    money_column,
)


class CashSessionStatus(str, Enum):
    OPEN = "open"
    CLOSED = "closed"


class CashSession(StoreScopedMixin, UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One till's working day: the float it started on and the count it ended on.

    Not append-only: the row is opened and later closed by mutation, because a
    session is state, not a ledger. The money truth it reports against lives in
    ``payments`` and ``payment_refunds``, which *are* append-only; the session
    only pins the window (``opened_at``..``closed_at``) those rows are summed
    over, so a disputed close can always be recomputed from the ledger.
    """

    __tablename__ = "cash_sessions"
    __table_args__ = (Index("ix_cash_sessions_store_id", "store_id", "status"),)

    opened_by_user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_by_user_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"))
    status: Mapped[CashSessionStatus] = mapped_column(enum_column(CashSessionStatus), nullable=False, default=CashSessionStatus.OPEN)
    opening_cash: Mapped[Decimal] = mapped_column(money_column(), nullable=False)
    counted_cash: Mapped[Decimal | None] = mapped_column(money_column())
    expected_cash: Mapped[Decimal | None] = mapped_column(money_column())
    difference: Mapped[Decimal | None] = mapped_column(money_column())
    closing_note: Mapped[str | None] = mapped_column(Text)
    #: Frozen at close: the ledger sums the window was computed over, so a later
    #: sync landing inside the window cannot silently rewrite history that a
    #: counted drawer already agreed -- or disagreed -- with.
    cash_in: Mapped[Decimal | None] = mapped_column(money_column())
    cash_out: Mapped[Decimal | None] = mapped_column(money_column())

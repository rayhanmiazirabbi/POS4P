from __future__ import annotations

import enum
from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, Enum, ForeignKey, MetaData, Numeric, Uuid, event, func
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column
from uuid6 import uuid7


def enum_column[E: enum.Enum](enum_cls: type[E], length: int = 40) -> Enum:
    """Store the enum *value* rather than its Python member name.

    SQLAlchemy defaults to persisting names (``ACTIVE``), which would not match the
    lowercase literals in the shared ``@pharmacy/types`` unions (``active``).
    ``native_enum=False`` keeps the column a portable VARCHAR + CHECK so the same
    models run on PostgreSQL and on SQLite in tests.
    """
    return Enum(
        enum_cls,
        native_enum=False,
        length=length,
        values_callable=lambda cls: [member.value for member in cls],
    )


def money_column() -> Numeric:
    """Money is 2 decimal places everywhere, backend and frontend alike.

    ``@pharmacy/money`` computes in integer cents, so a money column with more than
    2 places silently holds values the client cannot represent. Use
    :func:`quantity_column` for anything that is not a price or an amount.
    """
    return Numeric(18, 2)


def quantity_column() -> Numeric:
    """Stock counts and dosages need fractional units (half a strip, 2.5 mg).

    Not money: 4 places is a measurement precision, never a rounding decision,
    and these values never pass through ``@pharmacy/money``.
    """
    return Numeric(18, 4)


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention={
        "ix": "ix_%(column_0_label)s", "uq": "uq_%(table_name)s_%(column_0_name)s",
        "ck": "ck_%(table_name)s_%(constraint_name)s", "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
        "pk": "pk_%(table_name)s",
    })


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)


class UUIDPrimaryKeyMixin:
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid7)


class OrganizationScopedMixin:
    organization_id: Mapped[UUID] = mapped_column(ForeignKey("organizations.id"), nullable=False)


class StoreScopedMixin(OrganizationScopedMixin):
    store_id: Mapped[UUID] = mapped_column(ForeignKey("stores.id"), nullable=False)


class AppendOnlyMixin:
    """Marker for authoritative ledger rows that cannot be changed after insertion."""


@event.listens_for(Session, "before_flush")
def prevent_ledger_mutation(session: Session, *_: object) -> None:
    changed = session.dirty.union(session.deleted)
    if any(isinstance(row, AppendOnlyMixin) for row in changed):
        raise ValueError("append-only records cannot be updated or deleted")

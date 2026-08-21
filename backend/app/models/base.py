from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, MetaData, Uuid, event, func
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column
from uuid6 import uuid7


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

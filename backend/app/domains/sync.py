from __future__ import annotations

from datetime import datetime
from enum import Enum
from uuid import UUID

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, OrganizationScopedMixin, StoreScopedMixin, TimestampMixin, UUIDPrimaryKeyMixin


class DeviceStatus(str, Enum):
    ACTIVE = "active"
    REVOKED = "revoked"


class Device(OrganizationScopedMixin, UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "devices"
    __table_args__ = (UniqueConstraint("organization_id", "device_key"),)

    store_id: Mapped[UUID] = mapped_column(ForeignKey("stores.id"), nullable=False)
    device_key: Mapped[str] = mapped_column(String(160), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    status: Mapped[DeviceStatus] = mapped_column(default=DeviceStatus.ACTIVE, nullable=False)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SyncEvent(OrganizationScopedMixin, UUIDPrimaryKeyMixin, Base):
    __tablename__ = "sync_events"
    __table_args__ = (UniqueConstraint("organization_id", "event_id"),)

    store_id: Mapped[UUID] = mapped_column(ForeignKey("stores.id"), nullable=False)
    device_id: Mapped[UUID] = mapped_column(ForeignKey("devices.id"), nullable=False)
    event_id: Mapped[UUID] = mapped_column(nullable=False)
    event_type: Mapped[str] = mapped_column(String(120), nullable=False)
    client_sequence: Mapped[int] = mapped_column(BigInteger, nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    applied: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(80))


class SyncCheckpoint(StoreScopedMixin, UUIDPrimaryKeyMixin, Base):
    __tablename__ = "sync_checkpoints"
    __table_args__ = (UniqueConstraint("store_id", "device_id"),)

    device_id: Mapped[UUID] = mapped_column(ForeignKey("devices.id"), nullable=False)
    last_server_sequence: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    last_client_sequence: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)

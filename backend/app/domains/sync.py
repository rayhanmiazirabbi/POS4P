from __future__ import annotations

from datetime import datetime
from enum import Enum
from uuid import UUID

from sqlalchemy import JSON, BigInteger, Boolean, DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import (
    Base,
    OrganizationScopedMixin,
    StoreScopedMixin,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
    enum_column,
)


class DeviceStatus(str, Enum):
    ACTIVE = "active"
    REVOKED = "revoked"


class Device(OrganizationScopedMixin, UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "devices"
    __table_args__ = (UniqueConstraint("organization_id", "device_key"),)

    store_id: Mapped[UUID] = mapped_column(ForeignKey("stores.id"), nullable=False)
    device_key: Mapped[str] = mapped_column(String(160), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    status: Mapped[DeviceStatus] = mapped_column(enum_column(DeviceStatus), default=DeviceStatus.ACTIVE, nullable=False)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SyncEvent(OrganizationScopedMixin, UUIDPrimaryKeyMixin, Base):
    __tablename__ = "sync_events"
    __table_args__ = (UniqueConstraint("organization_id", "event_id"),)

    store_id: Mapped[UUID] = mapped_column(ForeignKey("stores.id"), nullable=False)
    device_id: Mapped[UUID] = mapped_column(ForeignKey("devices.id"), nullable=False)
    event_id: Mapped[UUID] = mapped_column(nullable=False)
    event_type: Mapped[str] = mapped_column(String(120), nullable=False)
    client_sequence: Mapped[int] = mapped_column(BigInteger, nullable=False)
    #: Server clock, always. A device's own clock is unverifiable and often wrong
    #: after weeks offline, so it must not be able to order the server's own feed.
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    #: What the device *claimed*, kept only as evidence -- how long the event sat in
    #: an offline queue, and how far that terminal's clock has drifted.
    client_created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    applied: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    #: Why the last attempt failed. Set together with ``applied=False``; an event is
    #: retried until it applies, so this is the only record of what a device is
    #: stuck on. Cleared when the event finally applies.
    error_code: Mapped[str | None] = mapped_column(String(80))


class SyncCheckpoint(StoreScopedMixin, UUIDPrimaryKeyMixin, Base):
    __tablename__ = "sync_checkpoints"
    __table_args__ = (UniqueConstraint("store_id", "device_id"),)

    device_id: Mapped[UUID] = mapped_column(ForeignKey("devices.id"), nullable=False)
    last_server_sequence: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    last_client_sequence: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)


class StoreSequence(Base):
    """Per-store monotonic counters, one row per store.

    The feed sequence and the receipt number are deliberately *separate* columns.
    They look interchangeable -- both are "the next number for this store" -- but
    they are consumed by different events, so sharing one counter makes each series
    skip wherever the other advanced. Gapped receipt numbers are the expensive half:
    a shop cannot show an auditor a receipt book that jumps from R-00000004 to
    R-00000009 and account for the difference, because nothing was ever issued.
    """

    __tablename__ = "store_sequences"

    store_id: Mapped[UUID] = mapped_column(ForeignKey("stores.id"), primary_key=True)
    #: Ordering for the sync pull feed.
    last_sequence: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    #: Customer-facing receipt numbering; must stay gapless.
    last_receipt_sequence: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)


class SyncFeedItem(StoreScopedMixin, UUIDPrimaryKeyMixin, Base):
    """An applied change projected into the per-store pull feed.

    Rows come from either an offline device event (``device_id``/``sync_event_id``
    set) or the server-side outbox (``outbox_event_id`` set); the feed stays
    gap-free by server sequence either way.

    Exactly one of those two source columns is set, and each is unique, so a
    change can reach the feed only once no matter how often its producer runs.
    Both are nullable, and a unique constraint ignores NULLs in PostgreSQL and
    SQLite alike, so the two kinds of row do not constrain each other.
    """

    __tablename__ = "sync_feed_items"
    __table_args__ = (
        UniqueConstraint("store_id", "server_sequence"),
        # One feed row per ingested event. Ingest re-runs an event whose row says
        # ``applied=False`` -- a crash between the handler's commit and this write
        # leaves exactly that -- and two retries racing each other both took the
        # re-run path and both wrote a row, so every terminal in the shop pulled
        # the same sale twice.
        UniqueConstraint("sync_event_id", name="uq_sync_feed_items_sync_event_id"),
        # One feed row per outbox event, for the same reason on the pull side: two
        # devices pulling at once both read the same unpublished rows.
        UniqueConstraint("outbox_event_id", name="uq_sync_feed_items_outbox_event_id"),
    )

    device_id: Mapped[UUID | None] = mapped_column(ForeignKey("devices.id"))
    sync_event_id: Mapped[UUID | None] = mapped_column(ForeignKey("sync_events.id"))
    outbox_event_id: Mapped[UUID | None] = mapped_column(ForeignKey("outbox_events.id"))
    event_type: Mapped[str] = mapped_column(String(120), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    server_sequence: Mapped[int] = mapped_column(BigInteger, nullable=False)

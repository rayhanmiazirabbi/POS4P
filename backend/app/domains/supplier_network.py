from __future__ import annotations

from datetime import datetime
from enum import Enum
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import (
    Base,
    OrganizationScopedMixin,
    StoreScopedMixin,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
)


class NetworkInviteStatus(str, Enum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


class SupplierNetworkInvite(OrganizationScopedMixin, UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """An outbound onboarding invitation to a not-yet-connected supplier.

    The supplier accepts with the bearer token alone -- they hold no platform
    account -- so the token is the credential and must be stored hashed.
    """

    __tablename__ = "supplier_network_invites"

    supplier_name: Mapped[str] = mapped_column(String(180), nullable=False)
    contact_phone: Mapped[str | None] = mapped_column(String(32))
    contact_email: Mapped[str | None] = mapped_column(String(255))
    note: Mapped[str | None] = mapped_column(Text)
    status: Mapped[NetworkInviteStatus] = mapped_column(
        default=NetworkInviteStatus.PENDING, nullable=False
    )
    invited_by_user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    accepted_supplier_id: Mapped[UUID | None] = mapped_column(ForeignKey("suppliers.id"))
    invite_token_hash: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AcknowledgementStatus(str, Enum):
    REQUESTED = "requested"
    ACKNOWLEDGED = "acknowledged"
    DECLINED = "declined"
    CANCELLED = "cancelled"


class PurchaseAcknowledgement(StoreScopedMixin, UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A confirmed purchase sent to the supplier network for confirmation.

    One live acknowledgement per purchase: re-requesting supersedes the previous
    token so a leaked old link cannot confirm a superseded request.
    """

    __tablename__ = "purchase_acknowledgements"

    purchase_id: Mapped[UUID] = mapped_column(ForeignKey("purchases.id"), nullable=False)
    supplier_id: Mapped[UUID] = mapped_column(ForeignKey("suppliers.id"), nullable=False)
    status: Mapped[AcknowledgementStatus] = mapped_column(
        default=AcknowledgementStatus.REQUESTED, nullable=False
    )
    requested_by_user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    note: Mapped[str | None] = mapped_column(Text)
    response_note: Mapped[str | None] = mapped_column(Text)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

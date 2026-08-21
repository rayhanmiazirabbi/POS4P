from __future__ import annotations

from datetime import datetime
from enum import Enum
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, OrganizationScopedMixin, StoreScopedMixin, UUIDPrimaryKeyMixin


class PrescriptionStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    NEEDS_CLARIFICATION = "needs_clarification"


class Prescription(OrganizationScopedMixin, UUIDPrimaryKeyMixin, Base):
    __tablename__ = "prescriptions"

    customer_id: Mapped[UUID | None] = mapped_column(ForeignKey("customers.id"))
    order_id: Mapped[UUID | None] = mapped_column(ForeignKey("orders.id"))
    status: Mapped[PrescriptionStatus] = mapped_column(default=PrescriptionStatus.PENDING, nullable=False)
    prescriber_name: Mapped[str | None] = mapped_column(String(160))
    prescription_number: Mapped[str | None] = mapped_column(String(100))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class PrescriptionFile(OrganizationScopedMixin, UUIDPrimaryKeyMixin, Base):
    __tablename__ = "prescription_files"

    prescription_id: Mapped[UUID] = mapped_column(ForeignKey("prescriptions.id"), nullable=False)
    object_key: Mapped[str] = mapped_column(String(500), nullable=False)
    content_type: Mapped[str] = mapped_column(String(100), nullable=False)
    checksum: Mapped[str] = mapped_column(String(128), nullable=False)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class PrescriptionReview(StoreScopedMixin, UUIDPrimaryKeyMixin, Base):
    __tablename__ = "prescription_reviews"

    prescription_id: Mapped[UUID] = mapped_column(ForeignKey("prescriptions.id"), nullable=False)
    status: Mapped[PrescriptionStatus] = mapped_column(nullable=False)
    pharmacist_user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)
    reviewed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

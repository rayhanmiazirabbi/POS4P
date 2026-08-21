from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import Enum
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, JSON, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, OrganizationScopedMixin, UUIDPrimaryKeyMixin


class AIJobStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    NEEDS_REVIEW = "needs_review"


class AIJob(OrganizationScopedMixin, UUIDPrimaryKeyMixin, Base):
    __tablename__ = "ai_jobs"

    job_type: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[AIJobStatus] = mapped_column(default=AIJobStatus.QUEUED, nullable=False)
    input_reference: Mapped[dict] = mapped_column(JSON, nullable=False)
    provider: Mapped[str | None] = mapped_column(String(80))
    model_version: Mapped[str | None] = mapped_column(String(120))
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(5, 4))
    result: Mapped[dict | None] = mapped_column(JSON)
    error: Mapped[str | None] = mapped_column(Text)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AIConfirmation(OrganizationScopedMixin, UUIDPrimaryKeyMixin, Base):
    __tablename__ = "ai_confirmations"

    job_id: Mapped[UUID] = mapped_column(ForeignKey("ai_jobs.id"), nullable=False)
    confirmed_by_user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    decision: Mapped[str] = mapped_column(String(40), nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

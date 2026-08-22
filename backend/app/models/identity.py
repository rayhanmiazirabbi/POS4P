from __future__ import annotations

from datetime import datetime
from enum import Enum
from uuid import UUID

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Index, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin, enum_column


class RecordStatus(str, Enum):
    ACTIVE = "active"
    INACTIVE = "inactive"
    SUSPENDED = "suspended"


class Role(str, Enum):
    OWNER = "owner"
    MANAGER = "manager"
    CASHIER = "cashier"
    INVENTORY_STAFF = "inventory_staff"


class Organization(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "organizations"

    name: Mapped[str] = mapped_column(String(160), nullable=False)
    slug: Mapped[str] = mapped_column(String(160), unique=True, nullable=False)
    status: Mapped[RecordStatus] = mapped_column(enum_column(RecordStatus), default=RecordStatus.ACTIVE, nullable=False)
    settings: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)

    memberships: Mapped[list[OrganizationUser]] = relationship(back_populates="organization")
    stores: Mapped[list[Store]] = relationship(back_populates="organization")


class User(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "users"

    phone: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    display_name: Mapped[str] = mapped_column(String(160), nullable=False)
    status: Mapped[RecordStatus] = mapped_column(enum_column(RecordStatus), default=RecordStatus.ACTIVE, nullable=False)
    pin_hash: Mapped[str | None] = mapped_column(String(255))

    memberships: Mapped[list[OrganizationUser]] = relationship(back_populates="user")


class Store(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "stores"
    __table_args__ = (UniqueConstraint("organization_id", "code"),)

    organization_id: Mapped[UUID] = mapped_column(ForeignKey("organizations.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    code: Mapped[str] = mapped_column(String(40), nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), default="Asia/Dhaka", nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="BDT", nullable=False)
    status: Mapped[RecordStatus] = mapped_column(enum_column(RecordStatus), default=RecordStatus.ACTIVE, nullable=False)
    settings: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)

    organization: Mapped[Organization] = relationship(back_populates="stores")
    memberships: Mapped[list[StoreUser]] = relationship(back_populates="store")


class OrganizationUser(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "organization_users"
    __table_args__ = (UniqueConstraint("organization_id", "user_id"), Index("ix_org_users_org", "organization_id"))

    organization_id: Mapped[UUID] = mapped_column(ForeignKey("organizations.id"), nullable=False)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    role: Mapped[Role] = mapped_column(enum_column(Role), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    organization: Mapped[Organization] = relationship(back_populates="memberships")
    user: Mapped[User] = relationship(back_populates="memberships")


class StoreUser(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "store_users"
    __table_args__ = (UniqueConstraint("store_id", "user_id"), Index("ix_store_users_store", "store_id"))

    store_id: Mapped[UUID] = mapped_column(ForeignKey("stores.id"), nullable=False)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    role: Mapped[Role] = mapped_column(enum_column(Role), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    store: Mapped[Store] = relationship(back_populates="memberships")


class AuthChallenge(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "auth_challenges"
    __table_args__ = (Index("ix_auth_challenges_destination", "destination", "created_at"),)

    destination: Mapped[str] = mapped_column(String(64), nullable=False)
    purpose: Mapped[str] = mapped_column(String(40), default="login", nullable=False)
    challenge_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Session(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "sessions"

    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    organization_id: Mapped[UUID] = mapped_column(ForeignKey("organizations.id"), nullable=False)
    store_id: Mapped[UUID | None] = mapped_column(ForeignKey("stores.id"))
    refresh_token_hash: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    device_id: Mapped[UUID | None] = mapped_column()
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

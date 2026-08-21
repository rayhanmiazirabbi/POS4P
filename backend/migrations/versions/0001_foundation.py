"""create foundation identity and cross-cutting tables

Revision ID: 0001_foundation
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001_foundation"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    uuid = sa.UUID()
    op.create_table("organizations", sa.Column("id", uuid, primary_key=True), sa.Column("name", sa.String(160), nullable=False), sa.Column("slug", sa.String(160), nullable=False, unique=True), sa.Column("status", sa.String(20), nullable=False), sa.Column("settings", sa.JSON(), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False), sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False))
    op.create_table("users", sa.Column("id", uuid, primary_key=True), sa.Column("phone", sa.String(32), nullable=False, unique=True), sa.Column("display_name", sa.String(160), nullable=False), sa.Column("status", sa.String(20), nullable=False), sa.Column("pin_hash", sa.String(255)), sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False), sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False))
    op.create_table("stores", sa.Column("id", uuid, primary_key=True), sa.Column("organization_id", uuid, sa.ForeignKey("organizations.id"), nullable=False), sa.Column("name", sa.String(160), nullable=False), sa.Column("code", sa.String(40), nullable=False), sa.Column("timezone", sa.String(64), nullable=False), sa.Column("currency", sa.String(3), nullable=False), sa.Column("status", sa.String(20), nullable=False), sa.Column("settings", sa.JSON(), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False), sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False), sa.UniqueConstraint("organization_id", "code"))
    op.create_table("organization_users", sa.Column("id", uuid, primary_key=True), sa.Column("organization_id", uuid, sa.ForeignKey("organizations.id"), nullable=False), sa.Column("user_id", uuid, sa.ForeignKey("users.id"), nullable=False), sa.Column("role", sa.String(30), nullable=False), sa.Column("active", sa.Boolean(), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False), sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False), sa.UniqueConstraint("organization_id", "user_id"))
    op.create_table("store_users", sa.Column("id", uuid, primary_key=True), sa.Column("store_id", uuid, sa.ForeignKey("stores.id"), nullable=False), sa.Column("user_id", uuid, sa.ForeignKey("users.id"), nullable=False), sa.Column("role", sa.String(30), nullable=False), sa.Column("active", sa.Boolean(), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False), sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False), sa.UniqueConstraint("store_id", "user_id"))
    op.create_table("auth_challenges", sa.Column("id", uuid, primary_key=True), sa.Column("destination", sa.String(64), nullable=False), sa.Column("challenge_hash", sa.String(255), nullable=False), sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False), sa.Column("consumed_at", sa.DateTime(timezone=True)), sa.Column("attempts", sa.Integer(), nullable=False))
    op.create_table("sessions", sa.Column("id", uuid, primary_key=True), sa.Column("user_id", uuid, sa.ForeignKey("users.id"), nullable=False), sa.Column("organization_id", uuid, sa.ForeignKey("organizations.id"), nullable=False), sa.Column("store_id", uuid, sa.ForeignKey("stores.id")), sa.Column("refresh_token_hash", sa.String(255), nullable=False, unique=True), sa.Column("device_id", uuid), sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False), sa.Column("revoked_at", sa.DateTime(timezone=True)), sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False), sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False))
    for name, columns in (("audit_logs", [("organization_id", uuid, "organizations.id"), ("store_id", uuid, "stores.id"), ("actor_user_id", uuid, "users.id")],), ("outbox_events", [("organization_id", uuid, "organizations.id")],), ("idempotency_records", [("organization_id", uuid, "organizations.id")],)):
        cols = [sa.Column("id", uuid, primary_key=True)]
        for column, type_, reference in columns: cols.append(sa.Column(column, type_, sa.ForeignKey(reference), nullable=column != "organization_id"))
        if name == "audit_logs": cols += [sa.Column("device_id", uuid), sa.Column("action", sa.String(120), nullable=False), sa.Column("entity_type", sa.String(120), nullable=False), sa.Column("entity_id", uuid), sa.Column("request_id", sa.String(80), nullable=False), sa.Column("before_data", sa.JSON()), sa.Column("after_data", sa.JSON()), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False)]
        elif name == "outbox_events": cols += [sa.Column("event_type", sa.String(120), nullable=False), sa.Column("aggregate_type", sa.String(120), nullable=False), sa.Column("aggregate_id", uuid, nullable=False), sa.Column("payload", sa.JSON(), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.Column("published_at", sa.DateTime(timezone=True))]
        else: cols += [sa.Column("key", sa.String(128), nullable=False), sa.Column("request_hash", sa.String(128), nullable=False), sa.Column("response_status", sa.Integer()), sa.Column("response_body", sa.JSON()), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.UniqueConstraint("organization_id", "key")]
        op.create_table(name, *cols)


def downgrade() -> None:
    for table in ("idempotency_records", "outbox_events", "audit_logs", "sessions", "auth_challenges", "store_users", "organization_users", "stores", "users", "organizations"): op.drop_table(table)

"""add device authorization and auth challenge/session hardening columns

Revision ID: 0002_auth
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_auth"
down_revision: str | None = "0001_foundation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    uuid = sa.UUID()

    # Challenges gain a purpose and a creation timestamp; the rate limiter counts
    # rows per destination inside a window, which needs both the index and the column.
    op.add_column(
        "auth_challenges",
        sa.Column("purpose", sa.String(40), nullable=False, server_default="login"),
    )
    op.add_column(
        "auth_challenges",
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_auth_challenges_destination", "auth_challenges", ["destination", "created_at"]
    )

    # Brute-force state for PIN login lives on the user so a lockout survives restarts.
    op.add_column(
        "users", sa.Column("pin_attempts", sa.Integer(), nullable=False, server_default="0")
    )
    op.add_column("users", sa.Column("pin_locked_until", sa.DateTime(timezone=True)))

    # A session exists between OTP verification and tenant selection, before the
    # organization it will belong to has been chosen (or, for a new owner, created).
    op.alter_column("sessions", "organization_id", existing_type=uuid, nullable=True)
    op.add_column("sessions", sa.Column("rotated_from_hash", sa.String(255)))
    op.create_index("ix_sessions_rotated_from_hash", "sessions", ["rotated_from_hash"])

    op.create_table(
        "devices",
        sa.Column("id", uuid, primary_key=True),
        sa.Column("organization_id", uuid, sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("store_id", uuid, sa.ForeignKey("stores.id"), nullable=False),
        sa.Column("device_key", sa.String(160), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("status", sa.String(40), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("organization_id", "device_key"),
    )


def downgrade() -> None:
    op.drop_table("devices")
    op.drop_index("ix_sessions_rotated_from_hash", table_name="sessions")
    op.drop_column("sessions", "rotated_from_hash")
    op.alter_column("sessions", "organization_id", existing_type=sa.UUID(), nullable=False)
    op.drop_column("users", "pin_locked_until")
    op.drop_column("users", "pin_attempts")
    op.drop_index("ix_auth_challenges_destination", table_name="auth_challenges")
    op.drop_column("auth_challenges", "created_at")
    op.drop_column("auth_challenges", "purpose")

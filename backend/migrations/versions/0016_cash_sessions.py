"""Cash drawer sessions: opening float, close count, expected difference.

Revision ID: 0016_cash_sessions
Revises: 0015_continuous_adoption_sales
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0016_cash_sessions"
down_revision: str | None = "0015_continuous_adoption_sales"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "cash_sessions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("organization_id", sa.Uuid(), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("store_id", sa.Uuid(), sa.ForeignKey("stores.id"), nullable=False),
        sa.Column("opened_by_user_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True)),
        sa.Column("closed_by_user_id", sa.Uuid(), sa.ForeignKey("users.id")),
        sa.Column("status", sa.String(40), nullable=False, server_default="open"),
        sa.Column("opening_cash", sa.Numeric(18, 2), nullable=False),
        sa.Column("counted_cash", sa.Numeric(18, 2)),
        sa.Column("expected_cash", sa.Numeric(18, 2)),
        sa.Column("difference", sa.Numeric(18, 2)),
        sa.Column("closing_note", sa.Text()),
        sa.Column("cash_in", sa.Numeric(18, 2)),
        sa.Column("cash_out", sa.Numeric(18, 2)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_cash_sessions_store_id", "cash_sessions", ["store_id", "status"])


def downgrade() -> None:
    op.drop_index("ix_cash_sessions_store_id", table_name="cash_sessions")
    op.drop_table("cash_sessions")

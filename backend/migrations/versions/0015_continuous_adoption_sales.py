"""Continuous adoption intake and structured POS adjustments.

Revision ID: 0015_continuous_adoption_sales
Revises: 0014_medicine_search_trigrams
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0015_continuous_adoption_sales"
down_revision: str | None = "0014_medicine_search_trigrams"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("customers", sa.Column("advance_balance", sa.Numeric(18, 2), nullable=False, server_default="0"))
    for name in ("line_discount", "global_discount", "delivery_charge", "other_fee", "advance_applied"):
        op.add_column("sales", sa.Column(name, sa.Numeric(18, 2), nullable=False, server_default="0"))
    op.add_column("sales", sa.Column("other_fee_label", sa.String(120)))
    op.add_column("sales", sa.Column("advance_reference", sa.String(160)))
    op.add_column("sale_items", sa.Column("discount_mode", sa.String(20)))
    op.add_column("sale_items", sa.Column("discount_value", sa.Numeric(18, 2), nullable=False, server_default="0"))
    op.add_column("sale_items", sa.Column("discount_amount", sa.Numeric(18, 2), nullable=False, server_default="0"))
    op.add_column("sale_returns", sa.Column("advance_restored", sa.Numeric(18, 2), nullable=False, server_default="0"))
    op.create_table(
        "discount_approvals",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("organization_id", sa.Uuid(), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("store_id", sa.Uuid(), sa.ForeignKey("stores.id"), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("approved_by_user_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("discount_approvals")
    op.drop_column("sale_returns", "advance_restored")
    for name in ("discount_amount", "discount_value", "discount_mode"):
        op.drop_column("sale_items", name)
    for name in ("advance_reference", "other_fee_label", "advance_applied", "other_fee", "delivery_charge", "global_discount", "line_discount"):
        op.drop_column("sales", name)
    op.drop_column("customers", "advance_balance")

"""Loyalty redemption recorded on the sale it discounted.

Revision ID: 0018_sale_loyalty_redemption
Revises: 0017_payment_method_values
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0018_sale_loyalty_redemption"
down_revision: str | None = "0017_payment_method_values"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "sales",
        sa.Column("loyalty_points_redeemed", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "sales",
        sa.Column("loyalty_credit", sa.Numeric(18, 2), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("sales", "loyalty_credit")
    op.drop_column("sales", "loyalty_points_redeemed")

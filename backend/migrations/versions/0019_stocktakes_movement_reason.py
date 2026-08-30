"""Stocktake sessions and movement reasons.

Revision ID: 0019_stocktakes_movement_reason
Revises: 0018_sale_loyalty_redemption
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0019_stocktakes_movement_reason"
down_revision: str | None = "0018_sale_loyalty_redemption"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("inventory_movements", sa.Column("reason", sa.String(280)))
    op.create_table(
        "stocktakes",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("organization_id", sa.Uuid(), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("store_id", sa.Uuid(), sa.ForeignKey("stores.id"), nullable=False),
        sa.Column("note", sa.String(280)),
        sa.Column("status", sa.String(40), nullable=False, server_default="draft"),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("created_by_user_id", sa.Uuid(), sa.ForeignKey("users.id")),
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
    op.create_index("ix_stocktakes_store_status", "stocktakes", ["store_id", "status"])
    op.create_table(
        "stocktake_items",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("organization_id", sa.Uuid(), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("stocktake_id", sa.Uuid(), sa.ForeignKey("stocktakes.id"), nullable=False),
        sa.Column("store_product_id", sa.Uuid(), sa.ForeignKey("store_products.id"), nullable=False),
        sa.Column("counted_quantity", sa.Numeric(18, 4), nullable=False),
        sa.UniqueConstraint("stocktake_id", "store_product_id"),
    )


def downgrade() -> None:
    op.drop_table("stocktake_items")
    op.drop_index("ix_stocktakes_store_status", table_name="stocktakes")
    op.drop_table("stocktakes")
    op.drop_column("inventory_movements", "reason")

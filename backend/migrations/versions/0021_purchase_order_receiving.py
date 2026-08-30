"""Link supplier receipts to purchase orders for partial receiving.

Revision ID: 0021_purchase_order_receiving
Revises: 0020_unified_receiving
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.domains.purchase_orders import PurchaseOrderStatus
from app.models.base import enum_column

revision: str = "0021_purchase_order_receiving"
down_revision: str | None = "0020_unified_receiving"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_STATUS = enum_column(PurchaseOrderStatus)


def upgrade() -> None:
    # Rebuild in batch mode so SQLite's generated enum check constraint learns
    # the two new values as well as PostgreSQL's varchar constraint.
    with op.batch_alter_table("purchase_orders") as batch:
        batch.alter_column(
            "status",
            existing_type=sa.String(length=40),
            type_=_STATUS,
            existing_nullable=False,
        )
    with op.batch_alter_table("purchases") as batch:
        batch.add_column(sa.Column("purchase_order_id", sa.Uuid(), nullable=True))
        batch.create_foreign_key(
            op.f("fk_purchases_purchase_order_id_purchase_orders"),
            "purchase_orders",
            ["purchase_order_id"],
            ["id"],
        )
        batch.create_index("ix_purchases_purchase_order_id", ["purchase_order_id"])
    with op.batch_alter_table("purchase_items") as batch:
        batch.add_column(sa.Column("purchase_order_item_id", sa.Uuid(), nullable=True))
        batch.create_foreign_key(
            op.f("fk_purchase_items_purchase_order_item_id_purchase_order_items"),
            "purchase_order_items",
            ["purchase_order_item_id"],
            ["id"],
        )
        batch.create_index("ix_purchase_items_purchase_order_item_id", ["purchase_order_item_id"])


def downgrade() -> None:
    with op.batch_alter_table("purchase_items") as batch:
        batch.drop_index("ix_purchase_items_purchase_order_item_id")
        batch.drop_constraint(
            op.f("fk_purchase_items_purchase_order_item_id_purchase_order_items"),
            type_="foreignkey",
        )
        batch.drop_column("purchase_order_item_id")
    with op.batch_alter_table("purchases") as batch:
        batch.drop_index("ix_purchases_purchase_order_id")
        batch.drop_constraint(
            op.f("fk_purchases_purchase_order_id_purchase_orders"),
            type_="foreignkey",
        )
        batch.drop_column("purchase_order_id")
    previous = sa.Enum(
        "draft",
        "ordered",
        "closed",
        "cancelled",
        name="purchaseorderstatus",
        native_enum=False,
        length=40,
    )
    with op.batch_alter_table("purchase_orders") as batch:
        batch.alter_column(
            "status",
            existing_type=_STATUS,
            type_=previous,
            existing_nullable=False,
        )

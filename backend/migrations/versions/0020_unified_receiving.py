"""Unified supplier receiving, payments, and GRN numbering.

Revision ID: 0020_unified_receiving
Revises: 0019_stocktakes_movement_reason
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0020_unified_receiving"
down_revision: str | None = "0019_stocktakes_movement_reason"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("store_sequences", sa.Column("last_grn_sequence", sa.BigInteger(), nullable=False, server_default="0"))
    op.add_column("purchases", sa.Column("receipt_number", sa.String(80)))
    with op.batch_alter_table("purchases") as batch:
        batch.create_unique_constraint("uq_purchases_store_receipt_number", ["store_id", "receipt_number"])
    op.add_column("purchase_items", sa.Column("line_total", sa.Numeric(18, 2)))
    op.execute("UPDATE purchase_items SET line_total = ROUND(quantity * unit_cost, 2)")
    with op.batch_alter_table("purchase_items") as batch:
        batch.alter_column("line_total", nullable=False)
    op.add_column("supplier_ledger_entries", sa.Column("payment_method", sa.String(40)))
    op.add_column("supplier_ledger_entries", sa.Column("provider_reference", sa.String(160)))


def downgrade() -> None:
    op.drop_column("supplier_ledger_entries", "provider_reference")
    op.drop_column("supplier_ledger_entries", "payment_method")
    op.drop_column("purchase_items", "line_total")
    with op.batch_alter_table("purchases") as batch:
        batch.drop_constraint("uq_purchases_store_receipt_number", type_="unique")
    op.drop_column("purchases", "receipt_number")
    op.drop_column("store_sequences", "last_grn_sequence")

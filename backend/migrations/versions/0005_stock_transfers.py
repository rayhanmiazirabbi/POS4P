"""Phase four: branch stock transfer documents.

``stock_transfers`` and ``stock_transfer_items`` record the draft -> in_transit ->
received workflow between branches of one organization. The inventory movement
ledger stays the source of truth: shipping and receiving write ordinary TRANSFER
and RECEIPT movements, so these tables only carry the document state.

Revision ID: 0005_stock_transfers
Revises: 0004_sync_counters
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005_stock_transfers"
down_revision: str | None = "0004_sync_counters"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "stock_transfers",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("transfer_number", sa.String(60), nullable=False),
        sa.Column("from_store_id", sa.Uuid(), nullable=False),
        sa.Column("to_store_id", sa.Uuid(), nullable=False),
        # The model persists enum values (lowercase) via ``enum_column``, so the
        # migrated column must agree or the metadata comparison reports drift.
        sa.Column(
            "status",
            sa.Enum(
                "draft",
                "in_transit",
                "received",
                "cancelled",
                name="transferstatus",
                native_enum=False,
                length=40,
            ),
            nullable=False,
        ),
        sa.Column("shipped_at", sa.DateTime(timezone=True)),
        sa.Column("received_at", sa.DateTime(timezone=True)),
        sa.Column("cancelled_at", sa.DateTime(timezone=True)),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], name=op.f("fk_stock_transfers_organization_id_organizations")),
        sa.ForeignKeyConstraint(["from_store_id"], ["stores.id"], name=op.f("fk_stock_transfers_from_store_id_stores")),
        sa.ForeignKeyConstraint(["to_store_id"], ["stores.id"], name=op.f("fk_stock_transfers_to_store_id_stores")),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], name=op.f("fk_stock_transfers_created_by_user_id_users")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_stock_transfers")),
        sa.UniqueConstraint("organization_id", "transfer_number", name=op.f("uq_stock_transfers_organization_id")),
    )
    op.create_index(
        "ix_stock_transfers_org_status",
        "stock_transfers",
        ["organization_id", "status"],
    )
    op.create_table(
        "stock_transfer_items",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("transfer_id", sa.Uuid(), nullable=False),
        sa.Column("store_product_id", sa.Uuid(), nullable=False),
        sa.Column("quantity", sa.Numeric(18, 4), nullable=False),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], name=op.f("fk_stock_transfer_items_organization_id_organizations")),
        sa.ForeignKeyConstraint(["transfer_id"], ["stock_transfers.id"], name=op.f("fk_stock_transfer_items_transfer_id_stock_transfers")),
        sa.ForeignKeyConstraint(["store_product_id"], ["store_products.id"], name=op.f("fk_stock_transfer_items_store_product_id_store_products")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_stock_transfer_items")),
        sa.UniqueConstraint("transfer_id", "store_product_id", name=op.f("uq_stock_transfer_items_transfer_id")),
    )


def downgrade() -> None:
    op.drop_table("stock_transfer_items")
    op.drop_index("ix_stock_transfers_org_status", table_name="stock_transfers")
    op.drop_table("stock_transfers")
    sa.Enum(name="transferstatus", native_enum=False, length=40).drop(
        op.get_bind(), checkfirst=True
    )

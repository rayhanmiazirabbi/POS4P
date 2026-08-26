"""Catalogue-led adoption and purchase orders.

``catalog_products`` gains ``generic_name`` (searched alongside name/aliases) plus
nullable reference prices used only to prefill a shelf price on adoption. The
backfill copies the ingredient name onto rows with exactly one active ingredient,
so imported single-molecule medicines match on generic searches without anyone
retyping them; combination products stay NULL until edited.

``purchase_orders`` / ``purchase_order_items`` are the lightweight ordering
document that sits in front of the existing purchase flow. Stock still enters
only through ``purchases`` confirmation -- these tables carry intent, never stock.

Revision ID: 0011_catalog_adoption_purchase_orders
Revises: 0010_sync_feed_uniqueness
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.domains.purchase_orders import PurchaseOrderStatus
from app.models.base import enum_column

revision: str = "0011_catalog_adoption_purchase_orders"
down_revision: str | None = "0010_sync_feed_uniqueness"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_STATUS_TYPE = enum_column(PurchaseOrderStatus)

_BACKFILL_GENERIC_NAME = """
UPDATE catalog_products
SET generic_name = ai.name
FROM catalog_product_ingredients cpi
JOIN active_ingredients ai ON ai.id = cpi.active_ingredient_id
WHERE cpi.catalog_product_id = catalog_products.id
  AND (
    SELECT count(*)
    FROM catalog_product_ingredients x
    WHERE x.catalog_product_id = catalog_products.id
  ) = 1
"""


def upgrade() -> None:
    op.add_column("catalog_products", sa.Column("generic_name", sa.String(240), nullable=True))
    op.add_column("catalog_products", sa.Column("unit_price", sa.Numeric(18, 2), nullable=True))
    op.add_column("catalog_products", sa.Column("strip_price", sa.Numeric(18, 2), nullable=True))
    op.create_index(
        "ix_catalog_product_generic_name", "catalog_products", ["generic_name"], unique=False
    )
    op.execute(sa.text(_BACKFILL_GENERIC_NAME))

    op.create_table(
        "purchase_orders",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("store_id", sa.Uuid(), nullable=False),
        sa.Column("supplier_id", sa.Uuid(), nullable=True),
        sa.Column("status", _STATUS_TYPE.copy(), nullable=False),
        sa.Column("expected_at", sa.Date()),
        sa.Column("note", sa.Text()),
        sa.Column("ordered_at", sa.DateTime(timezone=True)),
        sa.Column("closed_at", sa.DateTime(timezone=True)),
        sa.Column("cancelled_at", sa.DateTime(timezone=True)),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], name=op.f("fk_purchase_orders_organization_id_organizations")),
        sa.ForeignKeyConstraint(["store_id"], ["stores.id"], name=op.f("fk_purchase_orders_store_id_stores")),
        sa.ForeignKeyConstraint(["supplier_id"], ["suppliers.id"], name=op.f("fk_purchase_orders_supplier_id_suppliers")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_purchase_orders")),
        sa.UniqueConstraint("organization_id", "idempotency_key", name=op.f("uq_purchase_orders_organization_id")),
    )
    op.create_table(
        "purchase_order_items",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("purchase_order_id", sa.Uuid(), nullable=False),
        sa.Column("catalog_product_id", sa.Uuid(), nullable=True),
        sa.Column("pharmacy_product_id", sa.Uuid(), nullable=True),
        sa.Column("name", sa.String(240), nullable=False),
        sa.Column("quantity", sa.Numeric(18, 4), nullable=False),
        sa.Column("est_unit_cost", sa.Numeric(18, 2)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["catalog_product_id"], ["catalog_products.id"], name=op.f("fk_purchase_order_items_catalog_product_id_catalog_products")),
        sa.ForeignKeyConstraint(["pharmacy_product_id"], ["pharmacy_products.id"], name=op.f("fk_purchase_order_items_pharmacy_product_id_pharmacy_products")),
        sa.ForeignKeyConstraint(["purchase_order_id"], ["purchase_orders.id"], name=op.f("fk_purchase_order_items_purchase_order_id_purchase_orders")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_purchase_order_items")),
    )


def downgrade() -> None:
    op.drop_table("purchase_order_items")
    op.drop_table("purchase_orders")
    op.drop_index("ix_catalog_product_generic_name", table_name="catalog_products")
    # Batch mode: SQLite cannot ALTER-table columns away.
    with op.batch_alter_table("catalog_products") as batch:
        batch.drop_column("strip_price")
        batch.drop_column("unit_price")
        batch.drop_column("generic_name")

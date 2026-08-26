"""Upstream identity for imported catalogue rows.

Bulk imports had no way to say "this upstream product *is* that catalogue row", so
every refresh re-derived the link by name/strength/dosage form. That matching is
lossy across releases, and a near miss forks a duplicate instead of updating in place. Duplicates
in ``catalog_products`` are shared by every tenant, and a fresh row starts at
``prescription_required = False``, which is how an Rx medicine becomes sellable
without a prescription. Recording the upstream key on first import makes every
later refresh exact.

``(source, external_id)`` is unique so one upstream slug can only ever claim one
product. Deliberately no unique on ``(catalog_product_id, source)``: a source may
list the same medicine under two slugs, and both should point here.

Revision ID: 0012_catalog_source_refs
Revises: 0011_catalog_adoption_po
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012_catalog_source_refs"
down_revision: str | None = "0011_catalog_adoption_po"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "catalog_source_refs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("catalog_product_id", sa.Uuid(), nullable=False),
        sa.Column("source", sa.String(40), nullable=False),
        sa.Column("external_id", sa.String(160), nullable=False),
        sa.ForeignKeyConstraint(["catalog_product_id"], ["catalog_products.id"], name=op.f("fk_catalog_source_refs_catalog_product_id_catalog_products")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_catalog_source_refs")),
        sa.UniqueConstraint("source", "external_id", name=op.f("uq_catalog_source_refs_source")),
    )


def downgrade() -> None:
    op.drop_table("catalog_source_refs")

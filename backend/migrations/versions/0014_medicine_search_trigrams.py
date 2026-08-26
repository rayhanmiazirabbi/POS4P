"""Add PostgreSQL trigram indexes for typo-tolerant medicine search.

Revision ID: 0014_medicine_search_trigrams
Revises: 0013_dgda_catalog_field_widths
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0014_medicine_search_trigrams"
down_revision: str | None = "0013_dgda_catalog_field_widths"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_INDEXES = {
    "ix_catalog_product_name_trgm": "catalog_products USING gist (lower(name) gist_trgm_ops)",
    "ix_catalog_product_generic_name_trgm": "catalog_products USING gist (lower(generic_name) gist_trgm_ops)",
    "ix_catalog_alias_alias_trgm": "catalog_aliases USING gist (lower(alias) gist_trgm_ops)",
    "ix_pharmacy_product_name_trgm": "pharmacy_products USING gist (lower(name) gist_trgm_ops)",
}


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    # Deliberately fails loudly when the deployer cannot install extensions: an
    # apparently working rollout that silently drops typo search is harder to find.
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    for name, expression in _INDEXES.items():
        op.execute(f"CREATE INDEX {name} ON {expression}")


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    for name in reversed(_INDEXES):
        op.execute(f"DROP INDEX IF EXISTS {name}")
    # pg_trgm may be shared by another feature; do not remove the extension.

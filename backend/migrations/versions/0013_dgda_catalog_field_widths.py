"""Widen catalogue composition fields for official DGDA combinations.

The national registry includes multi-vitamin and veterinary combination
products whose generic composition and strength exceed the original limits.

Revision ID: 0013_dgda_catalog_field_widths
Revises: 0012_catalog_source_refs
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013_dgda_catalog_field_widths"
down_revision: str | None = "0012_catalog_source_refs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("catalog_products") as batch_op:
        batch_op.alter_column(
            "generic_name",
            existing_type=sa.String(length=240),
            type_=sa.String(length=512),
            existing_nullable=True,
        )
        batch_op.alter_column(
            "strength",
            existing_type=sa.String(length=100),
            type_=sa.String(length=512),
            existing_nullable=True,
        )
    with op.batch_alter_table("active_ingredients") as batch_op:
        batch_op.alter_column(
            "name",
            existing_type=sa.String(length=160),
            type_=sa.String(length=512),
            existing_nullable=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("active_ingredients") as batch_op:
        batch_op.alter_column(
            "name",
            existing_type=sa.String(length=512),
            type_=sa.String(length=160),
            existing_nullable=False,
        )
    with op.batch_alter_table("catalog_products") as batch_op:
        batch_op.alter_column(
            "strength",
            existing_type=sa.String(length=512),
            type_=sa.String(length=100),
            existing_nullable=True,
        )
        batch_op.alter_column(
            "generic_name",
            existing_type=sa.String(length=512),
            type_=sa.String(length=240),
            existing_nullable=True,
        )

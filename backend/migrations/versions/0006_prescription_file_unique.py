"""Phase five: prescription files are deduplicated per prescription.

``(prescription_id, object_key)`` becomes unique so a retried upload metadata
call cannot stack duplicate rows for the same stored object.

Revision ID: 0006_prescription_file_unique
Revises: 0005_stock_transfers
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0006_prescription_file_unique"
down_revision: str | None = "0005_stock_transfers"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Batch mode: SQLite cannot ALTER-table a constraint into existence.
    with op.batch_alter_table("prescription_files") as batch:
        batch.create_unique_constraint(
            op.f("uq_prescription_files_prescription_id"),
            ["prescription_id", "object_key"],
        )


def downgrade() -> None:
    with op.batch_alter_table("prescription_files") as batch:
        batch.drop_constraint(
            op.f("uq_prescription_files_prescription_id"), type_="unique"
        )

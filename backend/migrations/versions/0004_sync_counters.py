"""Split the receipt counter off the sync feed counter; keep the client clock as evidence.

``store_sequences.last_sequence`` served both the sync feed and receipt numbering, so
each series skipped wherever the other advanced. Receipt numbers must be gapless to be
auditable, so they get their own column. Existing rows seed the new counter from the
shared one: the old numbers are already gapped and cannot be repaired retroactively, but
continuing from the high-water mark guarantees no number is ever issued twice.

``sync_events.client_created_at`` preserves the device's own timestamp now that
``received_at`` is taken from the server clock instead of the envelope.

Revision ID: 0004_sync_counters
Revises: 0003_domain_tables
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_sync_counters"
down_revision: str | None = "0003_domain_tables"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # The server default stays after backfill. It is needed to add a NOT NULL column to
    # a table that already has rows, and dropping it afterwards would need
    # ``ALTER COLUMN ... DROP DEFAULT``, which SQLite cannot do -- and the test suite
    # migrates a SQLite database. A leftover default of 0 on a counter is harmless: the
    # model supplies its own, and every writer sets the value explicitly.
    op.add_column(
        "store_sequences",
        sa.Column("last_receipt_sequence", sa.BigInteger(), nullable=False, server_default="0"),
    )
    # Seed from the shared counter so no receipt number can repeat one already printed.
    op.execute("UPDATE store_sequences SET last_receipt_sequence = last_sequence")
    op.add_column(
        "sync_events",
        sa.Column("client_created_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("sync_events", "client_created_at")
    op.drop_column("store_sequences", "last_receipt_sequence")

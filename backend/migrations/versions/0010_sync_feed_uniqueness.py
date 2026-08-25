"""Phase three hardening: one feed row per change, enforced by the database.

``sync_feed_items`` had nothing tying a row to the change that produced it, so
both producers could write twice. Ingest re-runs an event whose row still says
``applied=False``, and two racing retries both took that path; a pull publishes
unpublished outbox rows, and two devices pulling at once both read the same set.
Either way every terminal in the shop pulled the same sale twice.

``sync_event_id`` becomes unique, and ``outbox_event_id`` is added and made
unique for the pull side. Both stay nullable -- a row has exactly one source --
and a unique constraint ignores NULLs in PostgreSQL and SQLite alike, so the two
kinds of row do not constrain each other.

Existing duplicates are collapsed before the constraint goes on, keeping the
lowest ``server_sequence`` for each event: that is the sequence devices were
told about in the ack, and the one already past most cursors.

Revision ID: 0010_sync_feed_uniqueness
Revises: 0009_return_idempotency
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010_sync_feed_uniqueness"
down_revision: str | None = "0009_return_idempotency"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        sa.text(
            """
            DELETE FROM sync_feed_items
            WHERE sync_event_id IS NOT NULL
              AND server_sequence > (
                SELECT MIN(keep.server_sequence) FROM sync_feed_items AS keep
                WHERE keep.sync_event_id = sync_feed_items.sync_event_id
              )
            """
        )
    )
    # Batch mode: SQLite cannot ALTER-table a column or a constraint into existence.
    with op.batch_alter_table("sync_feed_items") as batch:
        batch.add_column(sa.Column("outbox_event_id", sa.Uuid(), nullable=True))
        batch.create_foreign_key(
            op.f("fk_sync_feed_items_outbox_event_id_outbox_events"),
            "outbox_events",
            ["outbox_event_id"],
            ["id"],
        )
        batch.create_unique_constraint(
            op.f("uq_sync_feed_items_sync_event_id"), ["sync_event_id"]
        )
        batch.create_unique_constraint(
            op.f("uq_sync_feed_items_outbox_event_id"), ["outbox_event_id"]
        )


def downgrade() -> None:
    with op.batch_alter_table("sync_feed_items") as batch:
        batch.drop_constraint(op.f("uq_sync_feed_items_outbox_event_id"), type_="unique")
        batch.drop_constraint(op.f("uq_sync_feed_items_sync_event_id"), type_="unique")
        batch.drop_constraint(
            op.f("fk_sync_feed_items_outbox_event_id_outbox_events"), type_="foreignkey"
        )
        batch.drop_column("outbox_event_id")

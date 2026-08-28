"""Payment methods become tenant-configured values, not a shared enum.

Revision ID: 0017_payment_method_values
Revises: 0016_cash_sessions
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0017_payment_method_values"
down_revision: str | None = "0016_cash_sessions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: The native enum the table was born with, and the only values it can hold.
LEGACY_VALUES = ("CASH", "BKASH", "NAGAD", "DUE")


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        # A shared enum type cannot hold one tenant's "rocket" without every
        # other tenant seeing it, so the column becomes a plain varchar the
        # application validates against per-organization settings.
        op.execute("ALTER TABLE payments ALTER COLUMN method TYPE varchar(40) USING method::text")
        op.execute("DROP TYPE paymentmethod")
    else:
        # SQLite (tests) has no ALTER COLUMN and no enum type, only the CHECK
        # constraint the ORM generated; a batch recreate replaces the column
        # with the unconstrained varchar the model now declares.
        with op.batch_alter_table("payments") as batch:
            batch.alter_column("method", existing_type=sa.String(9), type_=sa.String(40), existing_nullable=False)
    # The enum stored member names ("CASH"); the model speaks values ("cash"),
    # which is also what every wire contract and report has always used.
    op.execute("UPDATE payments SET method = lower(method)")


def downgrade() -> None:
    op.execute(
        "DELETE FROM payments WHERE lower(method) NOT IN "
        "('cash', 'bkash', 'nagad', 'due')"
    )
    op.execute(
        "UPDATE payments SET method = upper(method) WHERE lower(method) IN "
        "('cash', 'bkash', 'nagad', 'due')"
    )
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        paymentmethod = sa.Enum(*LEGACY_VALUES, name="paymentmethod")
        paymentmethod.create(bind, checkfirst=True)
        op.execute(
            "ALTER TABLE payments ALTER COLUMN method TYPE paymentmethod USING method::paymentmethod"
        )
    else:
        legacy_enum = sa.Enum(*LEGACY_VALUES, name="paymentmethod")
        with op.batch_alter_table("payments") as batch:
            batch.alter_column("method", existing_type=sa.String(40), type_=legacy_enum, existing_nullable=False)

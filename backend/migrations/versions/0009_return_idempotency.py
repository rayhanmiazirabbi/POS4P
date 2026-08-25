"""Phase three hardening: returns and refunds are idempotent at the table level.

``sale_returns`` and ``payment_refunds`` both carried an ``idempotency_key`` that
nothing enforced, and both were filled with a fresh UUID -- so the column
recorded nothing and the constraint that would have caught a duplicate did not
exist. A double-tapped Refund button paid the customer twice.

``(organization_id, idempotency_key)`` matches ``sales`` and ``payments``: the
key is a client-supplied token, unique only within a tenant.

Revision ID: 0009_return_idempotency
Revises: 0008_audit_tamper_evidence
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0009_return_idempotency"
down_revision: str | None = "0008_audit_tamper_evidence"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = ("sale_returns", "payment_refunds")


def upgrade() -> None:
    # Batch mode: SQLite cannot ALTER-table a constraint into existence.
    for table in _TABLES:
        with op.batch_alter_table(table) as batch:
            batch.create_unique_constraint(
                op.f(f"uq_{table}_organization_id"),
                ["organization_id", "idempotency_key"],
            )


def downgrade() -> None:
    for table in _TABLES:
        with op.batch_alter_table(table) as batch:
            batch.drop_constraint(op.f(f"uq_{table}_organization_id"), type_="unique")

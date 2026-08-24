"""Phase six hardening: tamper-evident audit signatures.

Revision ID: 0008_audit_tamper_evidence
Revises: 0007_phase_six_platform
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008_audit_tamper_evidence"
down_revision: str | None = "0007_phase_six_platform"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    #: Nullable so rows written before this migration read as unsigned rather
    # than failing verification forever.
    op.add_column(
        "audit_logs",
        sa.Column("entry_hash", sa.String(length=64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("audit_logs", "entry_hash")

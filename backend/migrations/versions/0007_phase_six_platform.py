"""Phase six: billing provider events, supplier network, and purchase acknowledgements.

Revision ID: 0007_phase_six_platform
Revises: 0006_prescription_file_unique
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.domains.supplier_network import PurchaseAcknowledgement, SupplierNetworkInvite

revision: str = "0007_phase_six_platform"
down_revision: str | None = "0006_prescription_file_unique"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: Borrowed straight from the model metadata so the persisted CHECK lists the
#: lowercase wire values the ORM writes, and Alembic sees zero type drift.
_INVITE_STATUS_TYPE = SupplierNetworkInvite.__table__.c.status.type
_ACK_STATUS_TYPE = PurchaseAcknowledgement.__table__.c.status.type


def upgrade() -> None:
    op.create_table(
        "billing_provider_events",
        sa.Column("provider_event_id", sa.String(length=160), nullable=False),
        sa.Column("event_type", sa.String(length=80), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_billing_provider_events_organization_id_organizations"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_billing_provider_events")),
        sa.UniqueConstraint(
            "provider_event_id", name=op.f("uq_billing_provider_events_provider_event_id")
        ),
    )
    op.create_table(
        "supplier_network_invites",
        sa.Column("supplier_name", sa.String(length=180), nullable=False),
        sa.Column("contact_phone", sa.String(length=32), nullable=True),
        sa.Column("contact_email", sa.String(length=255), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("status", _INVITE_STATUS_TYPE.copy(), nullable=False),
        sa.Column("invited_by_user_id", sa.Uuid(), nullable=False),
        sa.Column("accepted_supplier_id", sa.Uuid(), nullable=True),
        sa.Column("invite_token_hash", sa.String(length=128), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["accepted_supplier_id"],
            ["suppliers.id"],
            name=op.f("fk_supplier_network_invites_accepted_supplier_id_suppliers"),
        ),
        sa.ForeignKeyConstraint(
            ["invited_by_user_id"],
            ["users.id"],
            name=op.f("fk_supplier_network_invites_invited_by_user_id_users"),
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_supplier_network_invites_organization_id_organizations"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_supplier_network_invites")),
        sa.UniqueConstraint(
            "invite_token_hash", name=op.f("uq_supplier_network_invites_invite_token_hash")
        ),
    )
    op.create_table(
        "purchase_acknowledgements",
        sa.Column("purchase_id", sa.Uuid(), nullable=False),
        sa.Column("supplier_id", sa.Uuid(), nullable=False),
        sa.Column("status", _ACK_STATUS_TYPE.copy(), nullable=False),
        sa.Column("requested_by_user_id", sa.Uuid(), nullable=False),
        sa.Column("token_hash", sa.String(length=128), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("response_note", sa.Text(), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("store_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["purchase_id"],
            ["purchases.id"],
            name=op.f("fk_purchase_acknowledgements_purchase_id_purchases"),
        ),
        sa.ForeignKeyConstraint(
            ["supplier_id"],
            ["suppliers.id"],
            name=op.f("fk_purchase_acknowledgements_supplier_id_suppliers"),
        ),
        sa.ForeignKeyConstraint(
            ["requested_by_user_id"],
            ["users.id"],
            name=op.f("fk_purchase_acknowledgements_requested_by_user_id_users"),
        ),
        sa.ForeignKeyConstraint(
            ["store_id"], ["stores.id"], name=op.f("fk_purchase_acknowledgements_store_id_stores")
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_purchase_acknowledgements_organization_id_organizations"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_purchase_acknowledgements")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_purchase_acknowledgements_token_hash")),
    )


def downgrade() -> None:
    op.drop_table("purchase_acknowledgements")
    op.drop_table("supplier_network_invites")
    op.drop_table("billing_provider_events")

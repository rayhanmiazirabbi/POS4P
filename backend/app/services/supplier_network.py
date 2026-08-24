from __future__ import annotations

from datetime import timedelta
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.context import RequestContext
from app.domains.purchasing import Purchase, PurchaseStatus
from app.domains.supplier_network import (
    AcknowledgementStatus,
    NetworkInviteStatus,
    PurchaseAcknowledgement,
    SupplierNetworkInvite,
)
from app.domains.suppliers import Supplier, SupplierStatus
from app.errors import Conflict, NotFound
from app.models import OutboxEvent
from app.security import as_utc, generate_token, hash_token, utc_now
from app.services.audit import record_audit, redact


def _public_miss() -> NotFound:
    """Token endpoints must not reveal whether a token ever existed."""
    return NotFound("Invitation not found")


# --- onboarding invitations ---------------------------------------------------


async def create_invite(
    session: AsyncSession,
    context: RequestContext,
    payload,
    *,
    request_id: str,
) -> tuple[SupplierNetworkInvite, str]:
    """Open an onboarding invitation; the token is returned once and stored hashed."""
    name = payload.supplier_name.strip()
    active_clash = await session.scalar(
        select(Supplier.id).where(
            Supplier.organization_id == context.organization_id,
            Supplier.name == name,
            Supplier.status == SupplierStatus.ACTIVE,
        )
    )
    if active_clash is not None:
        # An inactive row with the same name is fine: acceptance will reactivate
        # it so its ledger history stays attached to the same identity.
        raise Conflict(f"Supplier '{name}' already exists in this organization")

    settings = get_settings()
    ttl = payload.expires_in_days or settings.supplier_invite_ttl_days
    token = generate_token()
    invite = SupplierNetworkInvite(
        organization_id=context.organization_id,
        supplier_name=name,
        contact_phone=payload.contact_phone,
        contact_email=payload.contact_email,
        note=payload.note,
        status=NetworkInviteStatus.PENDING,
        invited_by_user_id=context.user_id,
        invite_token_hash=hash_token(token),
        expires_at=utc_now() + timedelta(days=ttl),
    )
    session.add(invite)
    await session.flush()
    record_audit(
        session,
        context,
        action="supplier_network.invite_created",
        entity_type="supplier_network_invite",
        entity_id=invite.id,
        request_id=request_id,
        after=redact({"supplier_name": name, "expires_at": invite.expires_at.isoformat()}),
    )
    enqueue(session, context.organization_id, "supplier_network.invite_sent", "supplier_network_invite", invite.id, {"invite_id": str(invite.id)})
    await session.commit()
    return invite, token


async def list_invites(session: AsyncSession, context: RequestContext) -> list[SupplierNetworkInvite]:
    return list(
        await session.scalars(
            select(SupplierNetworkInvite)
            .where(SupplierNetworkInvite.organization_id == context.organization_id)
            .order_by(SupplierNetworkInvite.created_at.desc())
        )
    )


async def load_invite(
    session: AsyncSession, context: RequestContext, invite_id: UUID
) -> SupplierNetworkInvite:
    invite = await session.get(SupplierNetworkInvite, invite_id)
    if invite is None or invite.organization_id != context.organization_id:
        raise NotFound("Invitation not found")
    return invite


async def cancel_invite(
    session: AsyncSession,
    context: RequestContext,
    invite_id: UUID,
    *,
    request_id: str,
) -> SupplierNetworkInvite:
    invite = await load_invite(session, context, invite_id)
    if invite.status is not NetworkInviteStatus.PENDING:
        raise Conflict(f"A {invite.status.value} invitation cannot be cancelled")
    invite.status = NetworkInviteStatus.CANCELLED
    invite.decided_at = utc_now()
    record_audit(
        session,
        context,
        action="supplier_network.invite_cancelled",
        entity_type="supplier_network_invite",
        entity_id=invite.id,
        request_id=request_id,
        after={"status": invite.status.value},
    )
    enqueue(session, context.organization_id, "supplier_network.invite_cancelled", "supplier_network_invite", invite.id, {"invite_id": str(invite.id)})
    await session.commit()
    return invite


async def accept_invite(
    session: AsyncSession, token: str, *, contact_phone: str | None = None
) -> tuple[SupplierNetworkInvite, Supplier]:
    """Public token acceptance: creates (or reactivates) the supplier record.

    An expired or already-used token reads as missing so old links cannot be
    replayed into duplicate suppliers. A name clash with an inactive supplier
    reactivates that row instead of dead-ending the onboarding -- ledger history
    stays attached to the same identity.
    """
    token_hash = hash_token(token.strip())
    invite = await session.scalar(
        select(SupplierNetworkInvite).where(SupplierNetworkInvite.invite_token_hash == token_hash)
    )
    if invite is None:
        raise _public_miss()

    now = utc_now()
    expires_at = as_utc(invite.expires_at)
    if invite.status is not NetworkInviteStatus.PENDING or expires_at is None or expires_at <= now:
        raise _public_miss()

    supplier = await session.scalar(
        select(Supplier).where(
            Supplier.organization_id == invite.organization_id,
            Supplier.name == invite.supplier_name,
        )
    )
    if supplier is not None and supplier.status is SupplierStatus.ACTIVE:
        raise Conflict("This supplier is already connected")

    if supplier is None:
        supplier = Supplier(
            organization_id=invite.organization_id,
            name=invite.supplier_name,
            phone=contact_phone or invite.contact_phone,
            status=SupplierStatus.ACTIVE,
        )
        session.add(supplier)
        await session.flush()
    else:
        supplier.status = SupplierStatus.ACTIVE
        if contact_phone:
            supplier.phone = contact_phone

    invite.accepted_supplier_id = supplier.id
    invite.status = NetworkInviteStatus.ACCEPTED
    invite.decided_at = now

    system_context = RequestContext(
        organization_id=invite.organization_id,
        user_id=None,
        role=None,
    )
    record_audit(
        session,
        system_context,
        action="supplier_network.invite_accepted",
        entity_type="supplier_network_invite",
        entity_id=invite.id,
        request_id=f"network-invite:{invite.id}",
        after={"supplier_id": str(supplier.id)},
    )
    enqueue(
        session,
        invite.organization_id,
        "supplier_network.supplier_joined",
        "supplier",
        supplier.id,
        {"invite_id": str(invite.id), "supplier_id": str(supplier.id)},
    )
    await session.commit()
    return invite, supplier


# --- purchase acknowledgements -------------------------------------------------


async def load_acknowledgement_for_org(
    session: AsyncSession, context: RequestContext, acknowledgement_id: UUID
) -> PurchaseAcknowledgement:
    acknowledgement = await session.get(PurchaseAcknowledgement, acknowledgement_id)
    if acknowledgement is None or acknowledgement.organization_id != context.organization_id:
        raise NotFound("Acknowledgement not found")
    return acknowledgement


async def list_acknowledgements(
    session: AsyncSession,
    context: RequestContext,
    *,
    purchase_id: UUID | None = None,
    status: AcknowledgementStatus | None = None,
) -> list[PurchaseAcknowledgement]:
    scope = [PurchaseAcknowledgement.organization_id == context.organization_id]
    if purchase_id is not None:
        scope.append(PurchaseAcknowledgement.purchase_id == purchase_id)
    if status is not None:
        scope.append(PurchaseAcknowledgement.status == status)
    return list(
        await session.scalars(
            select(PurchaseAcknowledgement)
            .where(*scope)
            .order_by(PurchaseAcknowledgement.created_at.desc())
        )
    )


async def request_purchase_acknowledgement(
    session: AsyncSession,
    context: RequestContext,
    purchase_id: UUID,
    payload,
    *,
    request_id: str,
) -> tuple[PurchaseAcknowledgement, str]:
    """Send a confirmed purchase to the supplier for confirmation.

    Only confirmed purchases travel outward: asking a supplier to acknowledge a
    draft would leak numbers nobody agreed to. A new request supersedes any live
    one by cancelling it, so exactly one outstanding token exists per purchase.
    """
    purchase = await session.get(Purchase, purchase_id)
    if (
        purchase is None
        or purchase.organization_id != context.organization_id
        or (context.store_id is not None and purchase.store_id != context.store_id)
    ):
        raise NotFound("Purchase not found")
    if purchase.status is not PurchaseStatus.CONFIRMED:
        raise Conflict("Only confirmed purchases can be sent for acknowledgement")

    supplier = await session.get(Supplier, purchase.supplier_id)
    if supplier is None or supplier.organization_id != context.organization_id:
        raise NotFound("Supplier not found")

    stale = await session.scalars(
        select(PurchaseAcknowledgement).where(
            PurchaseAcknowledgement.purchase_id == purchase_id,
            PurchaseAcknowledgement.status == AcknowledgementStatus.REQUESTED,
        )
    )
    for old in stale:
        old.status = AcknowledgementStatus.CANCELLED
        old.decided_at = utc_now()

    token = generate_token()
    acknowledgement = PurchaseAcknowledgement(
        organization_id=purchase.organization_id,
        store_id=purchase.store_id,
        purchase_id=purchase.id,
        supplier_id=purchase.supplier_id,
        status=AcknowledgementStatus.REQUESTED,
        requested_by_user_id=context.user_id,
        token_hash=hash_token(token),
        note=payload.note,
    )
    session.add(acknowledgement)
    await session.flush()
    record_audit(
        session,
        context,
        action="supplier_network.acknowledgement_requested",
        entity_type="purchase_acknowledgement",
        entity_id=acknowledgement.id,
        request_id=request_id,
        after={"purchase_id": str(purchase.id), "supplier_id": str(purchase.supplier_id)},
    )
    enqueue(
        session,
        purchase.organization_id,
        "supplier_network.acknowledgement_requested",
        "purchase_acknowledgement",
        acknowledgement.id,
        {"acknowledgement_id": str(acknowledgement.id)},
    )
    await session.commit()
    return acknowledgement, token


async def decide_purchase_acknowledgement(session: AsyncSession, payload) -> PurchaseAcknowledgement:
    """Public token decision by the supplier; requested rows only."""
    token_hash = hash_token(payload.token.strip())
    acknowledgement = await session.scalar(
        select(PurchaseAcknowledgement).where(PurchaseAcknowledgement.token_hash == token_hash)
    )
    if acknowledgement is None:
        raise _public_miss()
    if acknowledgement.status is not AcknowledgementStatus.REQUESTED:
        raise _public_miss()

    target = AcknowledgementStatus(payload.decision)
    acknowledgement.status = target
    acknowledgement.response_note = payload.response_note
    acknowledgement.decided_at = utc_now()

    system_context = RequestContext(
        organization_id=acknowledgement.organization_id,
        user_id=acknowledgement.requested_by_user_id,
        role=None,
    )
    record_audit(
        session,
        system_context,
        action=f"supplier_network.acknowledgement_{target.value}",
        entity_type="purchase_acknowledgement",
        entity_id=acknowledgement.id,
        request_id=f"network-ack:{acknowledgement.id}",
        after={"purchase_id": str(acknowledgement.purchase_id), "decision": target.value},
    )
    enqueue(
        session,
        acknowledgement.organization_id,
        f"supplier_network.acknowledgement_{target.value}",
        "purchase_acknowledgement",
        acknowledgement.id,
        {"acknowledgement_id": str(acknowledgement.id)},
    )
    await session.commit()
    return acknowledgement


async def cancel_acknowledgement(
    session: AsyncSession,
    context: RequestContext,
    acknowledgement_id: UUID,
    *,
    request_id: str,
) -> PurchaseAcknowledgement:
    acknowledgement = await load_acknowledgement_for_org(session, context, acknowledgement_id)
    if acknowledgement.status is not AcknowledgementStatus.REQUESTED:
        raise Conflict(f"A {acknowledgement.status.value} request cannot be cancelled")
    acknowledgement.status = AcknowledgementStatus.CANCELLED
    acknowledgement.decided_at = utc_now()
    record_audit(
        session,
        context,
        action="supplier_network.acknowledgement_cancelled",
        entity_type="purchase_acknowledgement",
        entity_id=acknowledgement.id,
        request_id=request_id,
        after={"purchase_id": str(acknowledgement.purchase_id)},
    )
    await session.commit()
    return acknowledgement


def enqueue(
    session: AsyncSession,
    organization_id: UUID,
    event_type: str,
    aggregate_type: str,
    aggregate_id: UUID,
    payload: dict,
) -> OutboxEvent:
    """Stage the external notification; the worker delivers it after commit."""
    event = OutboxEvent(
        organization_id=organization_id,
        event_type=event_type,
        aggregate_type=aggregate_type,
        aggregate_id=aggregate_id,
        payload=payload,
        created_at=utc_now(),
    )
    session.add(event)
    return event


async def count_pending_for_supplier(
    session: AsyncSession, organization_id: UUID, supplier_id: UUID
) -> int:
    total = await session.scalar(
        select(func.count())
        .select_from(PurchaseAcknowledgement)
        .where(
            PurchaseAcknowledgement.organization_id == organization_id,
            PurchaseAcknowledgement.supplier_id == supplier_id,
            PurchaseAcknowledgement.status == AcknowledgementStatus.REQUESTED,
        )
    )
    return int(total or 0)

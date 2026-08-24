from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.purchasing import Purchase, PurchaseStatus
from app.domains.supplier_network import AcknowledgementStatus
from app.domains.suppliers import Supplier, SupplierStatus
from app.models import AuditLog, OutboxEvent, Role
from tests.conftest import access_token_for


def _headers(tenant: dict[str, Any], *, role: Role = Role.OWNER) -> dict[str, str]:
    token = access_token_for(
        session_id=tenant["session"].id,
        user_id=tenant["owner"].id,
        organization_id=tenant["organization"].id,
        role=role,
        store_id=tenant["store"].id,
    )
    return {"Authorization": f"Bearer {token}"}


async def _make_supplier(session: AsyncSession, tenant: dict[str, Any], name: str = "Med Wholesaler") -> Supplier:
    supplier = Supplier(organization_id=tenant["organization"].id, name=name)
    session.add(supplier)
    await session.flush()
    return supplier


async def _make_confirmed_purchase(
    session: AsyncSession, tenant: dict[str, Any], supplier: Supplier
) -> Purchase:
    purchase = Purchase(
        organization_id=tenant["organization"].id,
        store_id=tenant["store"].id,
        supplier_id=supplier.id,
        status=PurchaseStatus.CONFIRMED,
        total_amount=Decimal("120.00"),
        purchased_at=datetime.now(UTC).date(),
        idempotency_key=f"purchase-{uuid4().hex[:12]}",
    )
    session.add(purchase)
    await session.flush()
    return purchase


async def test_invite_lifecycle_creates_supplier_on_acceptance(
    client: AsyncClient, session: AsyncSession, tenant: dict[str, Any]
) -> None:
    created = await client.post(
        "/supplier-network/invites",
        json={"supplierName": "New Distributor", "contactPhone": "+8801800000000"},
        headers=_headers(tenant),
    )
    assert created.status_code == 201, created.text
    body = created.json()["data"]
    token = body["inviteToken"]
    assert body["status"] == "pending"

    # The plaintext token is returned exactly once.
    listed = await client.get("/supplier-network/invites", headers=_headers(tenant))
    assert all("inviteToken" not in invite for invite in listed.json()["data"]["items"])

    accepted = await client.post("/supplier-network/invites/accept", json={"token": token})
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["data"]["status"] == "accepted"
    assert accepted.json()["data"]["acceptedSupplierId"] is not None

    replayed = await client.post("/supplier-network/invites/accept", json={"token": token})
    assert replayed.status_code == 404

    audited = await session.scalar(
        select(AuditLog.id).where(AuditLog.action == "supplier_network.invite_accepted")
    )
    outboxed = await session.scalar(
        select(OutboxEvent.id).where(OutboxEvent.event_type == "supplier_network.supplier_joined")
    )
    assert audited is not None and outboxed is not None


async def test_duplicate_invite_name_is_rejected_but_inactive_suppliers_are_reactivated(
    client: AsyncClient, session: AsyncSession, tenant: dict[str, Any]
) -> None:
    existing = await _make_supplier(session, tenant, "Med Wholesaler")
    existing.status = SupplierStatus.INACTIVE
    await session.commit()

    invited = await client.post(
        "/supplier-network/invites",
        json={"supplierName": "Med Wholesaler", "contactPhone": "+8801811111111"},
        headers=_headers(tenant),
    )
    assert invited.status_code == 201, invited.text
    accepted = await client.post(
        "/supplier-network/invites/accept",
        json={"token": invited.json()["data"]["inviteToken"]},
    )
    assert accepted.status_code == 200
    # The acceptance committed in another session; re-read past this one's cache.
    refreshed = await session.scalar(
        select(Supplier).where(Supplier.id == existing.id).execution_options(populate_existing=True)
    )
    assert refreshed is not None and refreshed.status == SupplierStatus.ACTIVE

    # An ACTIVE supplier with the same name blocks a second invitation.
    clash = await client.post(
        "/supplier-network/invites",
        json={"supplierName": "Med Wholesaler"},
        headers=_headers(tenant),
    )
    assert clash.status_code == 409


async def test_cancelled_and_expired_invites_cannot_be_accepted(
    client: AsyncClient, session: AsyncSession, tenant: dict[str, Any]
) -> None:
    from app.domains.supplier_network import SupplierNetworkInvite

    first = await client.post(
        "/supplier-network/invites", json={"supplierName": "Ghost Ltd"}, headers=_headers(tenant)
    )
    cancelled = await client.post(
        f"/supplier-network/invites/{first.json()['data']['id']}/cancel",
        headers=_headers(tenant),
    )
    assert cancelled.status_code == 200
    dead = await client.post(
        "/supplier-network/invites/accept",
        json={"token": first.json()["data"]["inviteToken"]},
    )
    assert dead.status_code == 404

    # An expired-but-still-pending invitation reads as missing too.
    second = await client.post(
        "/supplier-network/invites", json={"supplierName": "Late Ltd"}, headers=_headers(tenant)
    )
    invite_id = UUID(second.json()["data"]["id"])
    row = await session.get(SupplierNetworkInvite, invite_id)
    row.expires_at = datetime.now(UTC) - timedelta(days=1)
    await session.commit()
    expired = await client.post(
        "/supplier-network/invites/accept",
        json={"token": second.json()["data"]["inviteToken"]},
    )
    assert expired.status_code == 404


async def test_acknowledgement_flow_from_request_to_decision(
    client: AsyncClient, session: AsyncSession, tenant: dict[str, Any]
) -> None:
    from app.context import RequestContext
    from app.schemas.supplier_network import AcknowledgementCreateRequest
    from app.services.supplier_network import request_purchase_acknowledgement

    supplier = await _make_supplier(session, tenant)
    purchase = await _make_confirmed_purchase(session, tenant, supplier)
    context = RequestContext(
        organization_id=tenant["organization"].id,
        user_id=tenant["owner"].id,
        role=Role.OWNER,
        store_id=tenant["store"].id,
    )

    # The request endpoint deliberately does not expose the token; the outbox
    # worker delivers it to the supplier. We mint it via the service boundary.
    ack, token = await request_purchase_acknowledgement(
        session,
        context,
        purchase.id,
        AcknowledgementCreateRequest(note="please confirm availability"),
        request_id="test-ack-request",
    )
    listed = await client.get(
        "/supplier-network/acknowledgements", headers=_headers(tenant, role=Role.MANAGER)
    )
    assert listed.status_code == 200
    assert listed.json()["data"]["items"][0]["status"] == "requested"
    assert all("tokenHash" not in item for item in listed.json()["data"]["items"])

    decided = await client.post(
        "/supplier-network/acknowledgements/decide",
        json={"token": token, "decision": "acknowledged", "responseNote": "ships tomorrow"},
    )
    assert decided.status_code == 200, decided.text
    assert decided.json()["data"]["status"] == "acknowledged"
    assert decided.json()["data"]["responseNote"] == "ships tomorrow"

    replay = await client.post(
        "/supplier-network/acknowledgements/decide",
        json={"token": token, "decision": "declined"},
    )
    assert replay.status_code == 404

    audited = await session.scalar(
        select(AuditLog.id).where(AuditLog.action == "supplier_network.acknowledgement_acknowledged")
    )
    outboxed = await session.scalar(
        select(OutboxEvent.id).where(OutboxEvent.event_type == "supplier_network.acknowledgement_acknowledged")
    )
    assert audited is not None and outboxed is not None
    assert str(ack.purchase_id)


async def test_draft_purchases_cannot_be_sent_for_acknowledgement(
    client: AsyncClient, session: AsyncSession, tenant: dict[str, Any]
) -> None:
    supplier = await _make_supplier(session, tenant)
    purchase = Purchase(
        organization_id=tenant["organization"].id,
        store_id=tenant["store"].id,
        supplier_id=supplier.id,
        status=PurchaseStatus.DRAFT,
        total_amount=Decimal("10.00"),
        purchased_at=datetime.now(UTC).date(),
        idempotency_key=f"draft-{uuid4().hex[:12]}",
    )
    session.add(purchase)
    await session.commit()

    response = await client.post(
        f"/supplier-network/purchases/{purchase.id}/acknowledgements",
        json={},
        headers=_headers(tenant, role=Role.MANAGER),
    )
    assert response.status_code == 409


async def test_new_request_supersedes_the_outstanding_one(
    client: AsyncClient, session: AsyncSession, tenant: dict[str, Any]
) -> None:
    from app.context import RequestContext
    from app.schemas.supplier_network import AcknowledgementCreateRequest
    from app.services.supplier_network import (
        cancel_acknowledgement,
        list_acknowledgements,
        request_purchase_acknowledgement,
    )

    supplier = await _make_supplier(session, tenant)
    purchase = await _make_confirmed_purchase(session, tenant, supplier)
    context = RequestContext(
        organization_id=tenant["organization"].id,
        user_id=tenant["owner"].id,
        role=Role.OWNER,
        store_id=tenant["store"].id,
    )

    first, _token_a = await request_purchase_acknowledgement(
        session, context, purchase.id, AcknowledgementCreateRequest(), request_id="r1"
    )
    second, _token_b = await request_purchase_acknowledgement(
        session, context, purchase.id, AcknowledgementCreateRequest(), request_id="r2"
    )
    rows = await list_acknowledgements(session, context, purchase_id=purchase.id)
    statuses = {row.id: row.status for row in rows}
    assert statuses[second.id] == AcknowledgementStatus.REQUESTED
    assert statuses[first.id] == AcknowledgementStatus.CANCELLED

    cancelled = await cancel_acknowledgement(session, context, second.id, request_id="r3")
    assert cancelled.status == AcknowledgementStatus.CANCELLED

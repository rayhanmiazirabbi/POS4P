from __future__ import annotations

import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.domains.billing import BillingPlan, OrganizationSubscription
from app.models import AuditLog, OutboxEvent, Role
from tests._phase4_helpers import role_headers
from tests.conftest import access_token_for

BASIC_ENTITLEMENTS = {
    "pos": True,
    "inventory": True,
    "reports": True,
    "ecommerce": True,
    "custom_domain": False,
}
PRO_ENTITLEMENTS = {**BASIC_ENTITLEMENTS, "custom_domain": True}


def _headers(tenant: dict[str, Any], *, role: Role = Role.OWNER) -> dict[str, str]:
    token = access_token_for(
        session_id=tenant["session"].id,
        user_id=tenant["owner"].id,
        organization_id=tenant["organization"].id,
        role=role,
        store_id=tenant["store"].id,
    )
    return {"Authorization": f"Bearer {token}"}


async def _seed_plans(session: AsyncSession) -> tuple[BillingPlan, BillingPlan]:
    basic = BillingPlan(
        code="basic",
        name="Basic",
        monthly_amount=Decimal("10.00"),
        entitlements=BASIC_ENTITLEMENTS,
    )
    pro = BillingPlan(
        code="pro",
        name="Pro",
        monthly_amount=Decimal("25.00"),
        entitlements=PRO_ENTITLEMENTS,
    )
    session.add_all([basic, pro])
    await session.commit()
    return basic, pro


def _sign(payload: dict[str, Any]) -> tuple[bytes, dict[str, str]]:
    body = json.dumps(payload, separators=(",", ":")).encode()
    signature = hmac.new(
        get_settings().billing_webhook_secret.encode(), body, hashlib.sha256
    ).hexdigest()
    return body, {"X-Webhook-Signature": signature, "Content-Type": "application/json"}


@pytest.fixture
async def plans(session: AsyncSession) -> tuple[BillingPlan, BillingPlan]:
    return await _seed_plans(session)


async def test_subscription_bootstraps_a_trial_on_first_read(
    client: AsyncClient, tenant: dict[str, Any], plans: tuple[BillingPlan, BillingPlan]
) -> None:
    response = await client.get("/billing/subscription", headers=_headers(tenant))
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["status"] == "trial"
    assert data["effectiveStatus"] == "trial"
    assert data["planCode"] == "basic"  # cheapest active plan becomes the starter


async def test_subscription_view_is_owner_only(
    client: AsyncClient,
    session: AsyncSession,
    tenant: dict[str, Any],
    plans: tuple[BillingPlan, BillingPlan],
) -> None:
    manager = await client.get(
        "/billing/subscription", headers=await role_headers(session, tenant, Role.MANAGER)
    )
    assert manager.status_code == 403


async def test_change_plan_issues_an_invoice_and_activates(
    client: AsyncClient,
    session: AsyncSession,
    tenant: dict[str, Any],
    plans: tuple[BillingPlan, BillingPlan],
) -> None:
    response = await client.post(
        "/billing/subscription/plan",
        json={"planCode": "pro"},
        headers=_headers(tenant),
    )
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["status"] == "active"
    assert data["planCode"] == "pro"

    invoices = await client.get("/billing/invoices", headers=_headers(tenant))
    items = invoices.json()["data"]["items"]
    assert len(items) == 1
    assert items[0]["status"] == "open"
    assert items[0]["amount"] == "25.00"

    audits = await session.scalar(
        select(AuditLog.id).where(AuditLog.action == "billing.subscription_changed")
    )
    assert audits is not None


async def test_cancel_is_idempotent_and_outboxed(
    client: AsyncClient,
    session: AsyncSession,
    tenant: dict[str, Any],
    plans: tuple[BillingPlan, BillingPlan],
) -> None:
    first = await client.post("/billing/subscription/cancel", headers=_headers(tenant))
    second = await client.post("/billing/subscription/cancel", headers=_headers(tenant))
    assert first.status_code == 200 and second.status_code == 200

    events = list(
        await session.scalars(
            select(OutboxEvent.id).where(OutboxEvent.event_type == "billing.subscription_cancelled")
        )
    )
    assert len(events) == 1  # the replayed cancel is a no-op, not a second notice


async def test_change_plan_with_idempotency_key_issues_one_invoice(
    client: AsyncClient,
    session: AsyncSession,
    tenant: dict[str, Any],
    plans: tuple[BillingPlan, BillingPlan],
) -> None:
    headers = {**_headers(tenant), "Idempotency-Key": "plan-change-00000001"}
    body = {"planCode": "pro"}

    first = await client.post("/billing/subscription/plan", json=body, headers=headers)
    assert first.status_code == 200, first.text

    replay = await client.post("/billing/subscription/plan", json=body, headers=headers)
    assert replay.status_code == 200, replay.text
    assert replay.json()["data"]["id"] == first.json()["data"]["id"]

    invoices = (await client.get("/billing/invoices", headers=_headers(tenant))).json()["data"]
    assert invoices["total"] == 1  # a double-submit must not double-bill

    conflicting = await client.post(
        "/billing/subscription/plan",
        json={"planCode": "basic"},
        headers={**_headers(tenant), "Idempotency-Key": "plan-change-00000001"},
    )
    assert conflicting.status_code == 409


async def test_webhook_rejects_missing_or_bad_signatures(
    client: AsyncClient, tenant: dict[str, Any], plans: tuple[BillingPlan, BillingPlan]
) -> None:
    payload = {"eventId": "evt-000000001", "type": "invoice.paid", "organizationId": str(tenant["organization"].id)}
    missing = await client.post("/billing/webhook", json=payload)
    assert missing.status_code == 401
    forged = await client.post(
        "/billing/webhook",
        content=json.dumps(payload).encode(),
        headers={"X-Webhook-Signature": "0" * 64, "Content-Type": "application/json"},
    )
    assert forged.status_code == 401


async def test_webhook_marks_invoices_paid_exactly_once(
    client: AsyncClient,
    session: AsyncSession,
    tenant: dict[str, Any],
    plans: tuple[BillingPlan, BillingPlan],
) -> None:
    await client.post(
        "/billing/subscription/plan", json={"planCode": "pro"}, headers=_headers(tenant)
    )
    event = {
        "eventId": "evt-pay-00000001",
        "type": "invoice.paid",
        "organizationId": str(tenant["organization"].id),
        "data": {},
    }
    body, headers = _sign(event)
    delivered = await client.post("/billing/webhook", content=body, headers=headers)
    replay = await client.post("/billing/webhook", content=body, headers=headers)

    assert delivered.status_code == 200, delivered.text
    assert delivered.json() == {"status": "processed"}
    assert replay.json() == {"status": "duplicate"}

    invoices = (await client.get("/billing/invoices", headers=_headers(tenant))).json()["data"]["items"]
    paid = [invoice for invoice in invoices if invoice["status"] == "paid"]
    assert len(paid) == 1

    subscription = (await client.get("/billing/subscription", headers=_headers(tenant))).json()["data"]
    assert subscription["status"] == "active"


async def test_payment_failure_enters_grace_then_reads_cancelled_after_it(
    client: AsyncClient,
    session: AsyncSession,
    tenant: dict[str, Any],
    plans: tuple[BillingPlan, BillingPlan],
) -> None:
    organization_id = tenant["organization"].id
    bootstrapped = await client.get("/billing/subscription", headers=_headers(tenant))
    assert bootstrapped.status_code == 200
    failure = {
        "eventId": "evt-fail-0000001",
        "type": "invoice.payment_failed",
        "organizationId": str(organization_id),
        "data": {},
    }
    body, headers = _sign(failure)
    response = await client.post("/billing/webhook", content=body, headers=headers)
    assert response.status_code == 200, response.text

    subscription = (await client.get("/billing/subscription", headers=_headers(tenant))).json()["data"]
    assert subscription["status"] == "past_due"
    assert subscription["effectiveStatus"] == "past_due"
    assert subscription["gracePeriodEnd"] is not None

    # Once the grace window closes the tenant reads as cancelled even before a
    # provider webhook bothers to say so.
    subscription_id = await session.scalar(select(OrganizationSubscription.id))
    record = await session.get(OrganizationSubscription, subscription_id)
    record.grace_period_end = datetime.now(UTC) - timedelta(days=1)
    await session.commit()

    expired = (await client.get("/billing/subscription", headers=_headers(tenant))).json()["data"]
    assert expired["effectiveStatus"] == "cancelled"


async def test_custom_domain_requires_plan_entitlement(
    client: AsyncClient,
    session: AsyncSession,
    tenant: dict[str, Any],
    plans: tuple[BillingPlan, BillingPlan],
) -> None:
    storefront = {"slug": "main", "displayName": "Main", "enabled": True}

    denied = await client.post(
        "/ecommerce/storefronts",
        json={**storefront, "customDomain": "meds.example.com"},
        headers=_headers(tenant),
    )
    assert denied.status_code == 403, denied.text

    upgraded = await client.post(
        "/billing/subscription/plan", json={"planCode": "pro"}, headers=_headers(tenant)
    )
    assert upgraded.status_code == 200

    allowed = await client.post(
        "/ecommerce/storefronts",
        json={**storefront, "customDomain": "meds.example.com"},
        headers=_headers(tenant),
    )
    assert allowed.status_code == 201, allowed.text


async def test_unknown_webhook_event_type_is_acknowledged_not_failed(
    client: AsyncClient,
    session: AsyncSession,
    tenant: dict[str, Any],
    plans: tuple[BillingPlan, BillingPlan],
) -> None:
    event = {
        "eventId": "evt-unknown-00001",
        "type": "pigeon.released",
        "organizationId": str(tenant["organization"].id),
        "data": {},
    }
    body, headers = _sign(event)
    # The trial must exist first; deliveries for tenants we cannot resolve are
    # rejected with a 404 so the provider drops them instead of retrying forever.
    bootstrapped = await client.get("/billing/subscription", headers=_headers(tenant))
    assert bootstrapped.status_code == 200
    response = await client.post("/billing/webhook", content=body, headers=headers)
    assert response.status_code == 200, response.text
    assert response.json() == {"status": "processed"}

    subscription = (await client.get("/billing/subscription", headers=_headers(tenant))).json()["data"]
    assert subscription["status"] == "trial"

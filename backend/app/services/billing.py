from __future__ import annotations

import hashlib
import hmac
import json
from datetime import timedelta
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.context import RequestContext
from app.domains.billing import (
    BillingInvoice,
    BillingPlan,
    BillingProviderEvent,
    OrganizationSubscription,
    SubscriptionStatus,
)
from app.errors import Conflict, Forbidden, NotFound, ValidationError
from app.models import OutboxEvent
from app.security import as_utc, utc_now
from app.services.audit import record_audit, redact
from app.services.idempotency import remember, replay

#: Features that keep working while an organization is cancelled or past its
#: grace window: reading the books must never require paying first.
CORE_ENTITLEMENTS = frozenset({"pos", "inventory", "reports"})

SYSTEM_REQUEST_PREFIX = "billing-webhook:"


def _system_context(organization_id: UUID) -> RequestContext:
    """Webhooks have no human actor; audits still need tenant scope and a marker."""
    return RequestContext(organization_id=organization_id, user_id=None, role=None)


async def list_plans(session: AsyncSession) -> list[BillingPlan]:
    return list(
        await session.scalars(
            select(BillingPlan).where(BillingPlan.active.is_(True)).order_by(BillingPlan.monthly_amount)
        )
    )


async def load_plan_by_code(session: AsyncSession, code: str) -> BillingPlan:
    plan = await session.scalar(select(BillingPlan).where(BillingPlan.code == code))
    if plan is None or not plan.active:
        raise NotFound("Billing plan not found")
    return plan


async def load_plan(session: AsyncSession, plan_id: UUID) -> BillingPlan:
    plan = await session.get(BillingPlan, plan_id)
    if plan is None:
        raise NotFound("Billing plan not found")
    return plan


async def load_subscription(
    session: AsyncSession, organization_id: UUID, *, bootstrap: bool = True
) -> OrganizationSubscription | None:
    """The organization's single subscription row, bootstrapping a trial lazily.

    Sign-up does not create billing state -- the first visit to billing (or the
    first entitlement check) seeds a trial on the cheapest active plan, so a
    tenant can exist before any plan does. With ``bootstrap=False`` the caller
    accepts ``None`` for an organization that has never touched billing.
    """
    subscription = await session.scalar(
        select(OrganizationSubscription)
        .where(OrganizationSubscription.organization_id == organization_id)
        .order_by(OrganizationSubscription.created_at.desc())
    )
    if subscription is not None or not bootstrap:
        return subscription

    plans = await list_plans(session)
    if not plans:
        raise NotFound("No billing plans are configured")
    settings = get_settings()
    subscription = OrganizationSubscription(
        organization_id=organization_id,
        plan_id=plans[0].id,
        status=SubscriptionStatus.TRIAL,
        current_period_end=utc_now() + timedelta(days=settings.billing_trial_days),
    )
    session.add(subscription)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise Conflict("Subscription already exists") from exc
    return subscription


async def subscription_overview(
    session: AsyncSession, context: RequestContext
) -> tuple[OrganizationSubscription, BillingPlan]:
    """Read path for the owner billing view; persists a lazily seeded trial."""
    subscription = await load_subscription(session, context.organization_id)
    if subscription is None:
        raise NotFound("Subscription not found")
    plan = await session.get(BillingPlan, subscription.plan_id)
    if plan is None:
        raise NotFound("Billing plan not found")
    await session.commit()
    return subscription, plan


def stored_status(subscription: OrganizationSubscription) -> SubscriptionStatus:
    """The persisted status as an enum, tolerating value-form strings."""
    return SubscriptionStatus(subscription.status)


def effective_status(subscription: OrganizationSubscription) -> SubscriptionStatus:
    """Resolve stored status against the clock.

    An expired period is not immediately fatal: the tenant slides into
    ``past_due`` until the grace window closes, then reads as ``cancelled`` even
    before a webhook says so -- gating cannot depend on the provider's mood.
    """
    now = utc_now()
    status = stored_status(subscription)
    if status in (SubscriptionStatus.TRIAL, SubscriptionStatus.ACTIVE):
        period_end = as_utc(subscription.current_period_end)
        if period_end is not None and period_end <= now:
            grace_end = as_utc(subscription.grace_period_end)
            if grace_end is not None and grace_end > now:
                return SubscriptionStatus.PAST_DUE
            return SubscriptionStatus.CANCELLED
    if status is SubscriptionStatus.PAST_DUE:
        grace_end = as_utc(subscription.grace_period_end)
        if grace_end is not None and grace_end <= now:
            return SubscriptionStatus.CANCELLED
    return status


def entitlements_of(plan: BillingPlan) -> dict:
    entitlements = plan.entitlements or {}
    return {**{name: True for name in CORE_ENTITLEMENTS}, **entitlements}


async def entitlements_for(session: AsyncSession, organization_id: UUID) -> dict | None:
    subscription = await load_subscription(session, organization_id)
    if subscription is None:
        return None
    plan = await session.get(BillingPlan, subscription.plan_id)
    return entitlements_of(plan) if plan is not None else None


async def ensure_entitlement(
    session: AsyncSession, organization_id: UUID, feature: str
) -> None:
    """Raise unless the organization's effective plan includes ``feature``.

    With no plans seeded the platform runs in disabled-billing mode (the launch
    posture), so the gate opens rather than bricking every tenant.
    """
    try:
        subscription = await load_subscription(session, organization_id)
    except NotFound:
        return
    if subscription is None:
        return
    status = effective_status(subscription)
    if status is SubscriptionStatus.CANCELLED and feature not in CORE_ENTITLEMENTS:
        raise Forbidden("Subscription is not active", code="FORBIDDEN")
    plan = await session.get(BillingPlan, subscription.plan_id)
    if plan is None:
        return
    if not entitlements_of(plan).get(feature, False):
        raise Forbidden(f"The '{feature}' feature requires a plan upgrade")


async def _next_invoice_number(session: AsyncSession, organization_id: UUID) -> str:
    prefix = f"INV-{utc_now().strftime('%Y%m')}-"
    numbers = await session.scalars(
        select(BillingInvoice.invoice_number).where(
            BillingInvoice.organization_id == organization_id,
            BillingInvoice.invoice_number.like(f"{prefix}%"),
        )
    )
    suffixes = [int(n[len(prefix) :]) for n in numbers if n[len(prefix) :].isdigit()]
    return f"{prefix}{max(suffixes, default=0) + 1:05d}"


_INVOICE_NUMBER_ATTEMPTS = 5


async def _issue_invoice(
    session: AsyncSession,
    context: RequestContext,
    subscription: OrganizationSubscription,
    amount,
    *,
    request_id: str,
) -> BillingInvoice:
    """Insert an invoice under a savepoint so a number clash retries instead of
    poisoning the surrounding transaction (the count+1 scheme races under
    concurrent plan changes; the unique constraint is the backstop)."""
    for _ in range(_INVOICE_NUMBER_ATTEMPTS):
        invoice = BillingInvoice(
            organization_id=context.organization_id,
            subscription_id=subscription.id,
            invoice_number=await _next_invoice_number(session, context.organization_id),
            amount=amount,
            issued_at=utc_now(),
            status="open",
        )
        session.add(invoice)
        try:
            async with session.begin_nested():
                await session.flush()
        except IntegrityError:
            continue
        record_audit(
            session,
            context,
            action="billing.invoice_issued",
            entity_type="billing_invoice",
            entity_id=invoice.id,
            request_id=request_id,
            after={"invoice_number": invoice.invoice_number, "amount": str(amount)},
        )
        return invoice
    raise Conflict("Could not allocate a unique invoice number")


async def change_plan(
    session: AsyncSession,
    context: RequestContext,
    payload,
    *,
    request_id: str,
    idempotency_key: str | None = None,
) -> tuple[OrganizationSubscription, BillingInvoice | None]:
    """Move the organization onto another plan, issuing the payable invoice.

    With an ``Idempotency-Key`` a replayed key returns the existing state
    instead of issuing a second invoice for the same intent.
    """
    payload_dict = payload.model_dump(by_alias=True)
    if idempotency_key:
        stored = await replay(session, context.organization_id, idempotency_key, payload_dict)
        if stored is not None:
            plan = await load_plan_by_code(session, stored["planCode"])
            subscription = await load_subscription(session, context.organization_id)
            if subscription is None:
                raise NotFound("Subscription not found")
            return subscription, None

    plan = await load_plan_by_code(session, payload.plan_code)
    subscription = await load_subscription(session, context.organization_id)
    if subscription is None:
        raise NotFound("Subscription not found")
    previous_plan_id = subscription.plan_id
    previous_status = SubscriptionStatus(subscription.status)

    settings = get_settings()
    subscription.plan_id = plan.id
    subscription.status = SubscriptionStatus.ACTIVE
    subscription.current_period_end = utc_now() + timedelta(days=settings.billing_period_days)
    subscription.grace_period_end = None
    invoice = await _issue_invoice(session, context, subscription, plan.monthly_amount, request_id=request_id)
    record_audit(
        session,
        context,
        action="billing.subscription_changed",
        entity_type="organization_subscription",
        entity_id=subscription.id,
        request_id=request_id,
        before={"plan_id": str(previous_plan_id), "status": previous_status.value},
        after=redact({"plan_code": plan.code, "status": subscription.status.value}),
    )
    enqueue_billing_outbox(
        session,
        context.organization_id,
        "billing.subscription_changed",
        {"plan_code": plan.code, "status": subscription.status.value},
    )
    if idempotency_key:
        remember(
            session,
            context.organization_id,
            idempotency_key,
            payload_dict,
            response_status=200,
            response_body={"planCode": plan.code},
        )
    await session.commit()
    return subscription, invoice


async def cancel_subscription(
    session: AsyncSession,
    context: RequestContext,
    *,
    request_id: str,
) -> OrganizationSubscription | None:
    subscription = await load_subscription(session, context.organization_id)
    if subscription is None:
        return None
    if SubscriptionStatus(subscription.status) is SubscriptionStatus.CANCELLED:
        return subscription
    before = subscription.status
    subscription.status = SubscriptionStatus.CANCELLED
    subscription.grace_period_end = None
    record_audit(
        session,
        context,
        action="billing.subscription_cancelled",
        entity_type="organization_subscription",
        entity_id=subscription.id,
        request_id=request_id,
        before={"status": SubscriptionStatus(before).value},
        after={"status": subscription.status.value},
    )
    enqueue_billing_outbox(
        session, context.organization_id, "billing.subscription_cancelled", {}
    )
    await session.commit()
    return subscription


async def list_invoices(
    session: AsyncSession, context: RequestContext, *, limit: int = 25, offset: int = 0
) -> tuple[list[BillingInvoice], int]:
    scope = (BillingInvoice.organization_id == context.organization_id,)
    total = await session.scalar(select(func.count()).select_from(BillingInvoice).where(*scope))
    rows = list(
        await session.scalars(
            select(BillingInvoice)
            .where(*scope)
            .order_by(BillingInvoice.issued_at.desc())
            .limit(limit)
            .offset(offset)
        )
    )
    return rows, int(total or 0)


# --- provider webhooks --------------------------------------------------------


def verify_webhook_signature(body: bytes, signature: str | None) -> bool:
    """HMAC-SHA256 hex digest over the exact received bytes."""
    if not signature:
        return False
    expected = hmac.new(
        get_settings().billing_webhook_secret.encode(), body, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(signature.strip().lower(), expected)


def enqueue_billing_outbox(
    session: AsyncSession, organization_id: UUID, event_type: str, payload: dict
) -> OutboxEvent:
    event = OutboxEvent(
        organization_id=organization_id,
        event_type=event_type,
        aggregate_type="billing",
        aggregate_id=organization_id,
        payload=payload,
        created_at=utc_now(),
    )
    session.add(event)
    return event


async def handle_provider_event(
    session: AsyncSession,
    event_id: str,
    event_type: str,
    organization_id: UUID | None,
    data: dict,
) -> bool:
    """Apply one signed provider delivery exactly once. Returns False on replay.

    The unique ``provider_event_id`` is the dedup boundary: providers retry
    deliveries that look unacknowledged, and every handler below must stay
    idempotent even beyond the replay short-circuit.
    """
    recorded = BillingProviderEvent(
        provider_event_id=event_id,
        organization_id=organization_id,
        event_type=event_type,
        payload={"data": data},
        received_at=utc_now(),
    )
    session.add(recorded)
    try:
        await session.flush()
    except IntegrityError:
        await session.rollback()
        return False

    if organization_id is None:
        await session.commit()
        return True
    subscription = await load_subscription(session, organization_id, bootstrap=False)
    if subscription is None:
        await session.rollback()
        raise NotFound("Subscription not found")

    context = _system_context(organization_id)
    request_id = f"{SYSTEM_REQUEST_PREFIX}{event_id}"
    now = utc_now()
    settings = get_settings()

    if event_type == "invoice.paid":
        subscription.status = SubscriptionStatus.ACTIVE
        subscription.grace_period_end = None
        subscription.current_period_end = max(
            as_utc(subscription.current_period_end) or now,
            now + timedelta(days=settings.billing_period_days),
        )
        open_invoices = list(
            await session.scalars(
                select(BillingInvoice)
                .where(
                    BillingInvoice.organization_id == organization_id,
                    BillingInvoice.status == "open",
                )
                .order_by(BillingInvoice.issued_at)
            )
        )
        for invoice in open_invoices:
            invoice.paid_at = now
            invoice.status = "paid"
            record_audit(
                session,
                context,
                action="billing.invoice_paid",
                entity_type="billing_invoice",
                entity_id=invoice.id,
                request_id=request_id,
                after={"invoice_number": invoice.invoice_number},
            )
        action = "billing.subscription_activated"
    elif event_type == "invoice.payment_failed":
        subscription.status = SubscriptionStatus.PAST_DUE
        subscription.grace_period_end = now + timedelta(days=settings.billing_grace_period_days)
        action = "billing.subscription_past_due"
    elif event_type == "subscription.cancelled":
        subscription.status = SubscriptionStatus.CANCELLED
        subscription.grace_period_end = None
        action = "billing.subscription_cancelled"
    else:
        # Unknown types are recorded and acknowledged so the provider stops
        # retrying; they must not fail the whole webhook stream.
        await session.commit()
        return True

    record_audit(
        session,
        context,
        action=action,
        entity_type="organization_subscription",
        entity_id=subscription.id,
        request_id=request_id,
        after={"status": SubscriptionStatus(subscription.status).value},
    )
    enqueue_billing_outbox(
        session,
        organization_id,
        f"provider.{event_type}",
        {"subscription_id": str(subscription.id)},
    )
    await session.commit()
    return True


def decode_payload(raw: bytes) -> dict:
    try:
        payload = json.loads(raw)
    except (ValueError, UnicodeDecodeError) as exc:
        raise ValidationError("Webhook body must be valid JSON") from exc
    if not isinstance(payload, dict):
        raise ValidationError("Webhook body must be a JSON object")
    return payload

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Query, Request, status

from app.context import RequestContext
from app.dependencies import (
    ContextDep,
    IdempotencyKeyDep,
    RequestIdDep,
    SessionDep,
    require_roles,
)
from app.errors import NotFound, Unauthorized
from app.models import Role
from app.schemas.base import Envelope, Page
from app.schemas.billing import (
    BillingInvoiceResponse,
    BillingPlanResponse,
    PlanChangeRequest,
    ProviderEventRequest,
    SubscriptionResponse,
)
from app.services import billing as service

router = APIRouter(prefix="/billing", tags=["Billing"])

OwnerDep = Annotated[RequestContext, Depends(require_roles(Role.OWNER))]


def _subscription_response(subscription: Any, plan: Any) -> SubscriptionResponse:
    return SubscriptionResponse(
        id=subscription.id,
        plan_id=plan.id,
        plan_code=plan.code,
        plan_name=plan.name,
        status=service.stored_status(subscription).value,
        effective_status=service.effective_status(subscription).value,
        current_period_end=subscription.current_period_end,
        grace_period_end=subscription.grace_period_end,
        entitlements=service.entitlements_of(plan),
    )


@router.get("/plans", response_model=Envelope[list[BillingPlanResponse]])
async def list_plans(
    session: SessionDep, context: ContextDep, request_id: RequestIdDep
) -> Envelope[list[BillingPlanResponse]]:
    plans = await service.list_plans(session)
    return Envelope(
        data=[BillingPlanResponse.model_validate(plan) for plan in plans], request_id=request_id
    )


@router.get("/subscription", response_model=Envelope[SubscriptionResponse])
async def read_subscription(
    session: SessionDep, context: OwnerDep, request_id: RequestIdDep
) -> Envelope[SubscriptionResponse]:
    subscription, plan = await service.subscription_overview(session, context)
    return Envelope(data=_subscription_response(subscription, plan), request_id=request_id)


@router.post("/subscription/plan", response_model=Envelope[SubscriptionResponse])
async def change_plan(
    payload: PlanChangeRequest,
    session: SessionDep,
    context: OwnerDep,
    request_id: RequestIdDep,
    idempotency_key: IdempotencyKeyDep,
) -> Envelope[SubscriptionResponse]:
    subscription, _invoice = await service.change_plan(
        session,
        context,
        payload,
        request_id=request_id,
        idempotency_key=idempotency_key,
    )
    if subscription is None:
        raise NotFound("Subscription not found")
    plan = await service.load_plan(session, subscription.plan_id)
    return Envelope(data=_subscription_response(subscription, plan), request_id=request_id)


@router.post("/subscription/cancel", response_model=Envelope[SubscriptionResponse])
async def cancel_subscription(
    session: SessionDep, context: OwnerDep, request_id: RequestIdDep
) -> Envelope[SubscriptionResponse]:
    subscription = await service.cancel_subscription(session, context, request_id=request_id)
    if subscription is None:
        raise NotFound("Subscription not found")
    plan = await service.load_plan(session, subscription.plan_id)
    return Envelope(data=_subscription_response(subscription, plan), request_id=request_id)


@router.get("/invoices", response_model=Envelope[Page[BillingInvoiceResponse]])
async def list_invoices(
    session: SessionDep,
    context: OwnerDep,
    request_id: RequestIdDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Envelope[Page[BillingInvoiceResponse]]:
    rows, total = await service.list_invoices(session, context, limit=limit, offset=offset)
    return Envelope(
        data=Page(
            items=[BillingInvoiceResponse.model_validate(invoice) for invoice in rows],
            total=total,
        ),
        request_id=request_id,
    )


@router.post("/webhook", status_code=status.HTTP_200_OK)
async def provider_webhook(
    request: Request,
    session: SessionDep,
    payload: ProviderEventRequest,
    x_webhook_signature: Annotated[str | None, Header(alias="X-Webhook-Signature")] = None,
) -> dict[str, str]:
    """Signature-checked, replay-safe entry point for the billing provider.

    Authentication is the HMAC over the raw bytes -- there is no user on this
    path to impersonate, so no bearer token is accepted.
    """
    body = await request.body()
    if not service.verify_webhook_signature(body, x_webhook_signature):
        raise Unauthorized("Webhook signature is invalid or missing")
    delivered = await service.handle_provider_event(
        session,
        event_id=payload.event_id,
        event_type=payload.type,
        organization_id=payload.organization_id,
        data=payload.data,
    )
    return {"status": "processed" if delivered else "duplicate"}

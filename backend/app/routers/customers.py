from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status

from app.context import RequestContext
from app.dependencies import (
    ContextDep,
    RequestIdDep,
    SessionDep,
    require_roles,
)
from app.models import Role
from app.schemas.base import Envelope, Page
from app.schemas.customers import (
    CustomerAddressCreate,
    CustomerAddressResponse,
    CustomerCreate,
    CustomerHistorySummary,
    CustomerPurchaseRow,
    CustomerResponse,
    CustomerUpdate,
)
from app.services import customers as service

router = APIRouter(prefix="/customers", tags=["Customers"])

OwnerManagerDep = Annotated[RequestContext, Depends(require_roles(Role.OWNER, Role.MANAGER))]


@router.get(
    "",
    response_model=Envelope[Page[CustomerResponse]],
    summary="Search customers by name prefix or phone substring",
)
async def list_customers(
    session: SessionDep,
    context: ContextDep,
    request_id: RequestIdDep,
    q: str | None = None,
    active: Annotated[
        bool | None,
        Query(description="Filter by active flag; omit for active only, null-safe via false."),
    ] = True,
    has_due: Annotated[
        bool | None,
        Query(alias="hasDue", description="Only customers who owe (true) or are settled (false)."),
    ] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Envelope[Page[CustomerResponse]]:
    rows, total = await service.search_customers(
        session, context, q=q, active=active, has_due=has_due, limit=limit, offset=offset
    )
    items = [CustomerResponse.model_validate(customer) for customer in rows]
    return Envelope(data=Page(items=items, total=total), request_id=request_id)


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=Envelope[CustomerResponse],
)
async def create_customer(
    payload: CustomerCreate,
    session: SessionDep,
    context: ContextDep,
    request_id: RequestIdDep,
) -> Envelope[CustomerResponse]:
    customer = await service.create_customer(session, context, payload, request_id=request_id)
    return Envelope(data=CustomerResponse.model_validate(customer), request_id=request_id)


@router.get("/{customer_id}", response_model=Envelope[CustomerResponse])
async def read_customer(
    customer_id: UUID,
    session: SessionDep,
    context: ContextDep,
    request_id: RequestIdDep,
) -> Envelope[CustomerResponse]:
    customer = await service.load_customer(session, context, customer_id)
    return Envelope(data=CustomerResponse.model_validate(customer), request_id=request_id)


@router.patch("/{customer_id}", response_model=Envelope[CustomerResponse])
async def update_customer(
    customer_id: UUID,
    payload: CustomerUpdate,
    session: SessionDep,
    context: ContextDep,
    request_id: RequestIdDep,
) -> Envelope[CustomerResponse]:
    customer = await service.update_customer(session, context, customer_id, payload, request_id=request_id)
    return Envelope(data=CustomerResponse.model_validate(customer), request_id=request_id)


@router.delete(
    "/{customer_id}",
    response_model=Envelope[CustomerResponse],
    summary="Soft-deactivate a customer (owner/manager only)",
)
async def deactivate_customer(
    customer_id: UUID,
    session: SessionDep,
    context: OwnerManagerDep,
    request_id: RequestIdDep,
) -> Envelope[CustomerResponse]:
    customer = await service.deactivate_customer(session, context, customer_id, request_id=request_id)
    return Envelope(data=CustomerResponse.model_validate(customer), request_id=request_id)


@router.get(
    "/{customer_id}/history",
    response_model=Envelope[CustomerHistorySummary],
    summary="Netted purchase history; lifetime spend is redacted by role",
)
async def read_customer_history(
    customer_id: UUID,
    session: SessionDep,
    context: ContextDep,
    request_id: RequestIdDep,
) -> Envelope[CustomerHistorySummary]:
    summary = await service.get_history_summary(session, context, customer_id)
    if not service.can_see_lifetime_spend(context):
        summary.total_spent = None
    return Envelope(data=summary, request_id=request_id)


@router.post(
    "/{customer_id}/due/rebuild",
    response_model=Envelope[CustomerResponse],
    summary="Recompute the due balance from the payment ledger (owner/manager only)",
)
async def rebuild_due_balance(
    customer_id: UUID,
    session: SessionDep,
    context: OwnerManagerDep,
    request_id: RequestIdDep,
) -> Envelope[CustomerResponse]:
    """Reconcile a drifted ``due_balance`` against the ``due`` tenders and refunds."""
    customer = await service.rebuild_due_balance(
        session, context, customer_id, request_id=request_id
    )
    return Envelope(data=CustomerResponse.model_validate(customer), request_id=request_id)


@router.post(
    "/{customer_id}/addresses",
    status_code=status.HTTP_201_CREATED,
    response_model=Envelope[CustomerAddressResponse],
)
async def create_address(
    customer_id: UUID,
    payload: CustomerAddressCreate,
    session: SessionDep,
    context: ContextDep,
    request_id: RequestIdDep,
) -> Envelope[CustomerAddressResponse]:
    address = await service.create_address(session, context, customer_id, payload, request_id=request_id)
    return Envelope(data=CustomerAddressResponse.model_validate(address), request_id=request_id)


@router.get(
    "/{customer_id}/addresses",
    response_model=Envelope[list[CustomerAddressResponse]],
)
async def list_addresses(
    customer_id: UUID,
    session: SessionDep,
    context: ContextDep,
    request_id: RequestIdDep,
) -> Envelope[list[CustomerAddressResponse]]:
    addresses = await service.list_addresses(session, context, customer_id)
    items = [CustomerAddressResponse.model_validate(address) for address in addresses]
    return Envelope(data=items, request_id=request_id)


@router.get(
    "/{customer_id}/purchases",
    response_model=Envelope[Page[CustomerPurchaseRow]],
    summary="Cross-store purchase history (owner/manager only)",
)
async def list_purchase_history(
    customer_id: UUID,
    session: SessionDep,
    context: OwnerManagerDep,
    request_id: RequestIdDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Envelope[Page[CustomerPurchaseRow]]:
    """Completed sales across every branch, newest first."""
    rows, total = await service.list_purchase_history(
        session, context, customer_id, limit=limit, offset=offset
    )
    items = [
        CustomerPurchaseRow(
            sale_id=sale.id,
            store_id=sale.store_id,
            receipt_number=sale.receipt_number,
            total=sale.total,
            status=str(sale.status.value) if hasattr(sale.status, "value") else str(sale.status),
            created_at=sale.created_at,
        )
        for sale in rows
    ]
    return Envelope(data=Page(items=items, total=total), request_id=request_id)

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status

from app.context import RequestContext
from app.dependencies import (
    ContextDep,
    IdempotencyKeyDep,
    RequestIdDep,
    SessionDep,
    require_roles,
)
from app.errors import ValidationError
from app.models import Role
from app.schemas.base import Envelope, Page
from app.schemas.suppliers import (
    LedgerEntryCreateRequest,
    LedgerEntryResponse,
    SupplierBalanceResponse,
    SupplierCreateRequest,
    SupplierProductCreateRequest,
    SupplierProductResponse,
    SupplierResponse,
    SupplierStatusUpdateRequest,
    SupplierUpdateRequest,
)
from app.services import suppliers as service

router = APIRouter(prefix="/suppliers", tags=["Suppliers"])

SupplierManagerDep = Annotated[RequestContext, Depends(require_roles(Role.OWNER, Role.MANAGER))]
AdjusterDep = Annotated[RequestContext, Depends(require_roles(Role.OWNER, Role.MANAGER))]


@router.get(
    "",
    response_model=Envelope[Page[SupplierResponse]],
    summary="List the organization's suppliers",
)
async def list_suppliers(
    session: SessionDep, context: ContextDep, request_id: RequestIdDep
) -> Envelope[Page[SupplierResponse]]:
    items = [
        SupplierResponse.model_validate(supplier)
        for supplier in await service.list_suppliers(session, context)
    ]
    return Envelope(data=Page(items=items, total=len(items)), request_id=request_id)


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=Envelope[SupplierResponse],
)
async def create_supplier(
    payload: SupplierCreateRequest,
    session: SessionDep,
    context: SupplierManagerDep,
    request_id: RequestIdDep,
) -> Envelope[SupplierResponse]:
    supplier = await service.create_supplier(session, context, payload, request_id=request_id)
    return Envelope(data=SupplierResponse.model_validate(supplier), request_id=request_id)


@router.get("/{supplier_id}", response_model=Envelope[SupplierResponse])
async def read_supplier(
    supplier_id: UUID,
    session: SessionDep,
    context: ContextDep,
    request_id: RequestIdDep,
) -> Envelope[SupplierResponse]:
    supplier = await service.load_supplier(session, context, supplier_id)
    return Envelope(data=SupplierResponse.model_validate(supplier), request_id=request_id)


@router.patch("/{supplier_id}", response_model=Envelope[SupplierResponse])
async def update_supplier(
    supplier_id: UUID,
    payload: SupplierUpdateRequest,
    session: SessionDep,
    context: SupplierManagerDep,
    request_id: RequestIdDep,
) -> Envelope[SupplierResponse]:
    supplier = await service.update_supplier(
        session, context, supplier_id, payload, request_id=request_id
    )
    return Envelope(data=SupplierResponse.model_validate(supplier), request_id=request_id)


@router.patch("/{supplier_id}/status", response_model=Envelope[SupplierResponse])
async def update_supplier_status(
    supplier_id: UUID,
    payload: SupplierStatusUpdateRequest,
    session: SessionDep,
    context: SupplierManagerDep,
    request_id: RequestIdDep,
) -> Envelope[SupplierResponse]:
    supplier = await service.update_supplier_status(
        session, context, supplier_id, payload, request_id=request_id
    )
    return Envelope(data=SupplierResponse.model_validate(supplier), request_id=request_id)


@router.get("/{supplier_id}/products", response_model=Envelope[Page[SupplierProductResponse]])
async def list_supplier_products(
    supplier_id: UUID,
    session: SessionDep,
    context: ContextDep,
    request_id: RequestIdDep,
) -> Envelope[Page[SupplierProductResponse]]:
    items = [
        SupplierProductResponse.model_validate(mapping)
        for mapping in await service.list_supplier_products(session, context, supplier_id)
    ]
    return Envelope(data=Page(items=items, total=len(items)), request_id=request_id)


@router.post(
    "/{supplier_id}/products",
    status_code=status.HTTP_201_CREATED,
    response_model=Envelope[SupplierProductResponse],
)
async def create_supplier_product(
    supplier_id: UUID,
    payload: SupplierProductCreateRequest,
    session: SessionDep,
    context: SupplierManagerDep,
    request_id: RequestIdDep,
) -> Envelope[SupplierProductResponse]:
    mapping = await service.create_supplier_product(
        session, context, supplier_id, payload, request_id=request_id
    )
    return Envelope(data=SupplierProductResponse.model_validate(mapping), request_id=request_id)


@router.post(
    "/{supplier_id}/payments",
    response_model=Envelope[LedgerEntryResponse],
    summary="Record a payment to the supplier (reduces the payable balance)",
)
async def record_payment(
    supplier_id: UUID,
    payload: LedgerEntryCreateRequest,
    session: SessionDep,
    context: Annotated[RequestContext, Depends(require_roles(Role.OWNER, Role.MANAGER))],
    request_id: RequestIdDep,
    idempotency_key: IdempotencyKeyDep,
) -> Envelope[LedgerEntryResponse]:
    if not idempotency_key:
        raise ValidationError("Idempotency-Key header required")
    entry = await service.record_supplier_payment(
        session,
        context,
        supplier_id,
        payload,
        idempotency_key=idempotency_key,
        request_id=request_id,
    )
    return Envelope(data=LedgerEntryResponse.model_validate(entry), request_id=request_id)


@router.post(
    "/{supplier_id}/adjustments",
    response_model=Envelope[LedgerEntryResponse],
    summary="Book a signed ledger adjustment (owner/manager only)",
)
async def record_adjustment(
    supplier_id: UUID,
    payload: LedgerEntryCreateRequest,
    session: SessionDep,
    context: AdjusterDep,
    request_id: RequestIdDep,
    idempotency_key: IdempotencyKeyDep,
) -> Envelope[LedgerEntryResponse]:
    if not idempotency_key:
        raise ValidationError("Idempotency-Key header required")
    entry = await service.record_supplier_adjustment(
        session,
        context,
        supplier_id,
        payload,
        idempotency_key=idempotency_key,
        request_id=request_id,
    )
    return Envelope(data=LedgerEntryResponse.model_validate(entry), request_id=request_id)


@router.get(
    "/{supplier_id}/ledger",
    response_model=Envelope[Page[LedgerEntryResponse]],
    summary="Ledger entries newest-first",
)
async def list_ledger(
    supplier_id: UUID,
    session: SessionDep,
    context: ContextDep,
    request_id: RequestIdDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Envelope[Page[LedgerEntryResponse]]:
    items, total = await service.list_ledger(
        session, context, supplier_id, limit=limit, offset=offset
    )
    return Envelope(
        data=Page(
            items=[LedgerEntryResponse.model_validate(entry) for entry in items], total=total
        ),
        request_id=request_id,
    )


@router.get("/{supplier_id}/balance", response_model=Envelope[SupplierBalanceResponse])
async def read_balance(
    supplier_id: UUID,
    session: SessionDep,
    context: ContextDep,
    request_id: RequestIdDep,
) -> Envelope[SupplierBalanceResponse]:
    supplier = await service.load_supplier(session, context, supplier_id)
    balance = await service.supplier_balance(
        session, supplier.id, organization_id=context.organization_id
    )
    return Envelope(
        data=SupplierBalanceResponse(supplier_id=supplier.id, balance=balance),
        request_id=request_id,
    )

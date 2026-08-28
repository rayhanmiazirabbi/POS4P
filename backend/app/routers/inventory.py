from __future__ import annotations

from decimal import Decimal
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, status
from sqlalchemy import select

from app.context import RequestContext
from app.dependencies import ContextDep, RequestIdDep, SessionDep, require_roles
from app.domains.inventory import InventoryBalance, InventoryBatch
from app.errors import ValidationError
from app.models import Role
from app.schemas.base import Envelope
from app.schemas.inventory import (
    AdjustmentRequest,
    BalanceResponse,
    BatchAvailableResponse,
    BatchResponse,
    ExpiringBatchResponse,
    InventoryIntakeRequest,
    InventoryIntakeResponse,
    LowStockResponse,
    MovementResponse,
    RebuildResultResponse,
    ReceiveBatchRequest,
    ReceiveBatchResponse,
    StockResponse,
    TransferCreateRequest,
    TransferResponse,
)
from app.services import inventory as service

router = APIRouter(prefix="/inventory", tags=["Inventory"])

#: Receiving is inventory work; adjustments are a management decision.
InventoryStaffDep = Annotated[
    RequestContext, Depends(require_roles(Role.OWNER, Role.MANAGER, Role.INVENTORY_STAFF))
]
StoreManagerDep = Annotated[RequestContext, Depends(require_roles(Role.OWNER, Role.MANAGER))]


async def require_idempotency_key(
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> str:
    if not idempotency_key or not 16 <= len(idempotency_key.strip()) <= 128:
        raise ValidationError("Idempotency-Key header is required", code="VALIDATION_ERROR")
    return idempotency_key.strip()


IdempotentDep = Annotated[str, Depends(require_idempotency_key)]


@router.post(
    "/intakes",
    status_code=status.HTTP_201_CREATED,
    response_model=Envelope[InventoryIntakeResponse],
    summary="Adopt or update a shelf item and receive stock atomically",
)
async def intake_inventory(
    payload: InventoryIntakeRequest,
    session: SessionDep,
    context: ContextDep,
    request_id: RequestIdDep,
    idempotency_key: IdempotentDep,
) -> Envelope[InventoryIntakeResponse]:
    data = await service.intake_inventory(
        session, context, payload, idempotency_key=idempotency_key, request_id=request_id
    )
    return Envelope(data=InventoryIntakeResponse.model_validate(data), request_id=request_id)


def _q(value: object) -> Decimal:
    return Decimal(value).quantize(Decimal("0.0001"))


def _balance(balance: InventoryBalance) -> BalanceResponse:
    on_hand, reserved = _q(balance.on_hand), _q(balance.reserved)
    return BalanceResponse(
        store_product_id=balance.store_product_id,
        on_hand=on_hand,
        reserved=reserved,
        available=on_hand - reserved,
    )


@router.post(
    "/receive",
    status_code=status.HTTP_201_CREATED,
    response_model=Envelope[ReceiveBatchResponse],
    summary="Receive a stock batch (idempotent via the Idempotency-Key header)",
)
async def receive_batch(
    payload: ReceiveBatchRequest,
    session: SessionDep,
    context: InventoryStaffDep,
    request_id: RequestIdDep,
    idempotency_key: IdempotentDep,
) -> Envelope[ReceiveBatchResponse]:
    batch, movement, balance = await service.receive_batch(
        session,
        context,
        payload.store_product_id,
        batch_number=payload.batch_number,
        expiry_date=payload.expiry_date,
        unit_cost=payload.unit_cost,
        quantity=payload.quantity,
        reference_type=payload.reference_type,
        reference_id=payload.reference_id,
        idempotency_key=idempotency_key,
        request_id=request_id,
        commit=True,
    )
    return Envelope(
        data=ReceiveBatchResponse(
            batch=BatchResponse.model_validate(batch),
            movement=MovementResponse.model_validate(movement),
            balance=_balance(balance),
        ),
        request_id=request_id,
    )


@router.get("/stock", response_model=Envelope[list[StockResponse]])
async def list_stock(
    session: SessionDep,
    context: ContextDep,
    request_id: RequestIdDep,
    store_id: Annotated[UUID, Query(alias="storeId")],
) -> Envelope[list[StockResponse]]:
    rows = await service.list_stock(session, context, store_id)
    items = [
        StockResponse(
            store_product_id=product.id,
            on_hand=_q(on_hand),
            reserved=_q(reserved),
            available=_q(on_hand - reserved),
            low_stock=on_hand - reserved < product.minimum_stock,
        )
        for product, on_hand, reserved in rows
    ]
    return Envelope(data=items, request_id=request_id)


@router.get(
    "/stock/{store_product_id}/batches",
    response_model=Envelope[list[BatchAvailableResponse]],
)
async def list_batches(
    store_product_id: UUID,
    session: SessionDep,
    context: ContextDep,
    request_id: RequestIdDep,
) -> Envelope[list[BatchAvailableResponse]]:
    await service.load_store_product(session, context, store_product_id)
    stocks = await service.list_batches_fefo(session, context, store_product_id)
    batches = {
        b.id: b
        for b in await session.scalars(
            select(InventoryBatch).where(InventoryBatch.store_product_id == store_product_id)
        )
    }
    today = service.now_utc().date()
    items = [
        BatchAvailableResponse(
            batch_id=(batch := batches[stock.batch_id]).id,
            batch_number=batch.batch_number,
            expiry_date=batch.expiry_date,
            received_at=batch.received_at,
            unit_cost=batch.unit_cost,
            available=stock.available,
            expired=batch.expiry_date is not None and batch.expiry_date < today,
        )
        for stock in stocks
    ]
    return Envelope(data=items, request_id=request_id)


@router.get("/expiring", response_model=Envelope[list[ExpiringBatchResponse]])
async def list_expiring(
    session: SessionDep,
    context: ContextDep,
    request_id: RequestIdDep,
    store_id: Annotated[UUID, Query(alias="storeId")],
    within_days: Annotated[int, Query(ge=0, alias="withinDays")] = 30,
) -> Envelope[list[ExpiringBatchResponse]]:
    rows = await service.expiring_batches(session, context, store_id, within_days=within_days)
    today = service.now_utc().date()
    items = []
    for product, batch, available in rows:
        days_left = (batch.expiry_date - today).days if batch.expiry_date else 0
        items.append(
            ExpiringBatchResponse(
                store_product_id=product.id,
                batch_id=batch.id,
                batch_number=batch.batch_number,
                expiry_date=batch.expiry_date,
                received_at=batch.received_at,
                unit_cost=batch.unit_cost,
                available=available,
                expired=days_left < 0,
                days_until_expiry=max(days_left, 0),
            )
        )
    return Envelope(data=items, request_id=request_id)


@router.get("/low-stock", response_model=Envelope[list[LowStockResponse]])
async def list_low_stock(
    session: SessionDep,
    context: ContextDep,
    request_id: RequestIdDep,
    store_id: Annotated[UUID, Query(alias="storeId")],
) -> Envelope[list[LowStockResponse]]:
    rows = await service.low_stock_products(session, context, store_id)
    items = [
        LowStockResponse(
            store_product_id=product.id,
            sku=product.sku,
            on_hand=available,
            minimum_stock=product.minimum_stock,
        )
        for product, available in rows
    ]
    return Envelope(data=items, request_id=request_id)


@router.post(
    "/adjustments",
    status_code=status.HTTP_201_CREATED,
    response_model=Envelope[BalanceResponse],
    summary="Apply a signed stock correction (owner/manager only)",
)
async def create_adjustment(
    payload: AdjustmentRequest,
    session: SessionDep,
    context: StoreManagerDep,
    request_id: RequestIdDep,
) -> Envelope[BalanceResponse]:
    _, balance = await service.adjust_stock(
        session,
        context,
        payload.store_product_id,
        quantity=payload.quantity,
        reason=payload.reason,
        batch_id=payload.batch_id,
        damage=payload.damage,
        request_id=request_id,
    )
    return Envelope(data=_balance(balance), request_id=request_id)


@router.post(
    "/rebuild",
    response_model=Envelope[RebuildResultResponse],
    summary="Rebuild balance projections from the movement ledger",
)
async def rebuild_balances(
    session: SessionDep,
    context: StoreManagerDep,
    request_id: RequestIdDep,
    store_id: Annotated[UUID, Query(alias="storeId")],
) -> Envelope[RebuildResultResponse]:
    from app.services.stores import load_store

    await load_store(session, context, store_id)
    rebuilt = await service.rebuild_balances_from_ledger(session, store_id, commit=True)
    return Envelope(
        data=RebuildResultResponse(store_id=store_id, rebuilt=len(rebuilt)),
        request_id=request_id,
    )


# --- branch transfers ---------------------------------------------------------


@router.get("/transfers", response_model=Envelope[list[TransferResponse]])
async def list_transfers(
    session: SessionDep,
    context: StoreManagerDep,
    request_id: RequestIdDep,
    status_filter: Annotated[str | None, Query(alias="statusFilter")] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Envelope[list[TransferResponse]]:
    """Transfers touching the caller's branch, newest first."""
    from app.domains.inventory import TransferStatus

    status_value = TransferStatus(status_filter) if status_filter else None
    rows, _total = await service.list_transfers(
        session, context, status_filter=status_value, limit=limit, offset=offset
    )
    return Envelope(
        data=[TransferResponse.model_validate(t) for t in rows], request_id=request_id
    )


@router.post(
    "/transfers",
    status_code=status.HTTP_201_CREATED,
    response_model=Envelope[TransferResponse],
    summary="Open a draft transfer between two branches (owner/manager only)",
)
async def create_transfer(
    payload: TransferCreateRequest,
    session: SessionDep,
    context: StoreManagerDep,
    request_id: RequestIdDep,
) -> Envelope[TransferResponse]:
    transfer = await service.create_transfer(
        session,
        context,
        from_store_id=payload.from_store_id,
        to_store_id=payload.to_store_id,
        items=[(line.store_product_id, line.quantity) for line in payload.items],
        transfer_number=payload.transfer_number,
        request_id=request_id,
    )
    return Envelope(data=TransferResponse.model_validate(transfer), request_id=request_id)


@router.post("/transfers/{transfer_id}/ship", response_model=Envelope[TransferResponse])
async def ship_transfer(
    transfer_id: UUID,
    session: SessionDep,
    context: StoreManagerDep,
    request_id: RequestIdDep,
) -> Envelope[TransferResponse]:
    """FEFO-allocate at the source branch and put the transfer in transit."""
    transfer = await service.ship_transfer(session, context, transfer_id, request_id=request_id)
    return Envelope(data=TransferResponse.model_validate(transfer), request_id=request_id)


@router.post("/transfers/{transfer_id}/receive", response_model=Envelope[TransferResponse])
async def receive_transfer(
    transfer_id: UUID,
    session: SessionDep,
    context: StoreManagerDep,
    request_id: RequestIdDep,
) -> Envelope[TransferResponse]:
    """Book the stock into the destination branch as fresh batches."""
    transfer = await service.receive_transfer(session, context, transfer_id, request_id=request_id)
    return Envelope(data=TransferResponse.model_validate(transfer), request_id=request_id)


@router.post("/transfers/{transfer_id}/cancel", response_model=Envelope[TransferResponse])
async def cancel_transfer(
    transfer_id: UUID,
    session: SessionDep,
    context: StoreManagerDep,
    request_id: RequestIdDep,
) -> Envelope[TransferResponse]:
    transfer = await service.cancel_transfer(session, context, transfer_id, request_id=request_id)
    return Envelope(data=TransferResponse.model_validate(transfer), request_id=request_id)

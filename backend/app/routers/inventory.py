from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, status
from sqlalchemy import select

from app.context import RequestContext
from app.dependencies import ContextDep, RequestIdDep, SessionDep, require_roles
from app.domains.inventory import InventoryBalance, InventoryBatch, InventoryMovementType
from app.errors import ValidationError
from app.models import Role
from app.schemas.base import Envelope, Page
from app.schemas.inventory import (
    AdjustmentRequest,
    BalanceResponse,
    BatchAvailableResponse,
    BatchResponse,
    ExpiringBatchResponse,
    InventoryIntakeRequest,
    InventoryIntakeResponse,
    LowStockResponse,
    MovementLedgerResponse,
    MovementResponse,
    RackRenameRequest,
    RackResponse,
    RebuildResultResponse,
    ReceiveBatchRequest,
    ReceiveBatchResponse,
    ReorderSuggestionResponse,
    StockResponse,
    StocktakeCreateRequest,
    StocktakeLineRequest,
    StocktakeLineResponse,
    StocktakeResponse,
    StocktakeSummaryResponse,
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


# --- movement ledger ------------------------------------------------------------


@router.get("/movements", response_model=Envelope[Page[MovementLedgerResponse]])
async def list_movements(
    session: SessionDep,
    context: ContextDep,
    request_id: RequestIdDep,
    store_product_id: Annotated[UUID | None, Query(alias="storeProductId")] = None,
    movement_type: Annotated[str | None, Query(alias="movementType")] = None,
    start: datetime | None = None,
    end: datetime | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Envelope[Page[MovementLedgerResponse]]:
    """The append-only stock ledger, newest first, for the caller's branch."""
    parsed_type = InventoryMovementType(movement_type) if movement_type else None
    rows, total = await service.list_movements(
        session,
        context,
        store_product_id=store_product_id,
        movement_type=parsed_type,
        start=start,
        end=end,
        limit=limit,
        offset=offset,
    )
    items = [
        MovementLedgerResponse(
            id=movement.id,
            store_product_id=movement.store_product_id,
            sku=product.sku,
            product_name=pharmacy_product.name if pharmacy_product else product.sku,
            batch_id=movement.batch_id,
            batch_number=batch.batch_number if batch else None,
            movement_type=movement.movement_type,
            quantity=movement.quantity,
            reason=movement.reason,
            reference_type=movement.reference_type,
            occurred_at=movement.occurred_at,
        )
        for movement, product, pharmacy_product, batch in rows
    ]
    return Envelope(data=Page(items=items, total=total), request_id=request_id)


# --- racks ----------------------------------------------------------------------


@router.get("/racks", response_model=Envelope[list[RackResponse]])
async def list_racks(
    session: SessionDep,
    context: ContextDep,
    request_id: RequestIdDep,
    store_id: Annotated[UUID, Query(alias="storeId")],
) -> Envelope[list[RackResponse]]:
    """Distinct rack labels on the branch's active shelf, with item counts."""
    rows = await service.list_racks(session, context, store_id)
    return Envelope(
        data=[RackResponse(rack=label, item_count=count) for label, count in rows],
        request_id=request_id,
    )


@router.post(
    "/racks/rename",
    response_model=Envelope[RackResponse],
    summary="Move every item on one rack label to another (owner/manager only)",
)
async def rename_rack(
    payload: RackRenameRequest,
    session: SessionDep,
    context: StoreManagerDep,
    request_id: RequestIdDep,
) -> Envelope[RackResponse]:
    moved = await service.rename_rack(
        session,
        context,
        payload.store_id,
        from_rack=payload.from_rack,
        to_rack=payload.to_rack,
        request_id=request_id,
    )
    return Envelope(
        data=RackResponse(rack=payload.to_rack.strip(), item_count=moved),
        request_id=request_id,
    )


# --- reorder suggestions ----------------------------------------------------------


@router.get("/reorder-suggestions", response_model=Envelope[list[ReorderSuggestionResponse]])
async def list_reorder_suggestions(
    session: SessionDep,
    context: ContextDep,
    request_id: RequestIdDep,
    store_id: Annotated[UUID, Query(alias="storeId")],
) -> Envelope[list[ReorderSuggestionResponse]]:
    """Below-minimum products with a suggested refill quantity."""
    rows = await service.reorder_suggestions(session, context, store_id)
    items = [
        ReorderSuggestionResponse(
            store_product_id=product.id,
            sku=product.sku,
            product_name=pharmacy_product.name if pharmacy_product else product.sku,
            available=available,
            minimum_stock=Decimal(product.minimum_stock),
            suggested_quantity=suggested,
        )
        for product, pharmacy_product, available, suggested in rows
    ]
    return Envelope(data=items, request_id=request_id)


# --- stocktakes -----------------------------------------------------------------


@router.get("/stocktakes", response_model=Envelope[list[StocktakeResponse]])
async def list_stocktakes(
    session: SessionDep,
    context: StoreManagerDep,
    request_id: RequestIdDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Envelope[list[StocktakeResponse]]:
    """Count sessions for the caller's branch, newest first (without lines)."""
    rows, _total = await service.list_stocktakes(session, context, limit=limit, offset=offset)
    return Envelope(
        data=[
            StocktakeResponse(
                id=stocktake.id,
                store_id=stocktake.store_id,
                status=stocktake.status,
                note=stocktake.note,
                created_at=stocktake.created_at,
                completed_at=stocktake.completed_at,
                lines=[],
            )
            for stocktake in rows
        ],
        request_id=request_id,
    )


@router.post(
    "/stocktakes",
    status_code=status.HTTP_201_CREATED,
    response_model=Envelope[StocktakeResponse],
    summary="Open a physical count session (owner/manager only)",
)
async def create_stocktake(
    payload: StocktakeCreateRequest,
    session: SessionDep,
    context: StoreManagerDep,
    request_id: RequestIdDep,
) -> Envelope[StocktakeResponse]:
    stocktake = await service.create_stocktake(session, context, note=payload.note, request_id=request_id)
    return Envelope(
        data=StocktakeResponse(
            id=stocktake.id,
            store_id=stocktake.store_id,
            status=stocktake.status,
            note=stocktake.note,
            created_at=stocktake.created_at,
            completed_at=stocktake.completed_at,
            lines=[],
        ),
        request_id=request_id,
    )


@router.get("/stocktakes/{stocktake_id}", response_model=Envelope[StocktakeResponse])
async def read_stocktake(
    stocktake_id: UUID,
    session: SessionDep,
    context: StoreManagerDep,
    request_id: RequestIdDep,
) -> Envelope[StocktakeResponse]:
    """One count session with each line's counted vs system quantity."""
    stocktake, lines = await service.stocktake_view(session, context, stocktake_id)
    return Envelope(data=_stocktake_response(stocktake, lines), request_id=request_id)


def _stocktake_response(
    stocktake: service.Stocktake,
    lines: list[tuple[service.StocktakeItem, service.StoreProduct, service.PharmacyProduct | None, Decimal]],
) -> StocktakeResponse:
    return StocktakeResponse(
        id=stocktake.id,
        store_id=stocktake.store_id,
        status=stocktake.status,
        note=stocktake.note,
        created_at=stocktake.created_at,
        completed_at=stocktake.completed_at,
        lines=[
            StocktakeLineResponse(
                store_product_id=item.store_product_id,
                sku=product.sku,
                product_name=pharmacy_product.name if pharmacy_product else product.sku,
                counted_quantity=item.counted_quantity,
                system_quantity=Decimal(item.counted_quantity) + variance,
                variance=variance,
            )
            for item, product, pharmacy_product, variance in lines
        ],
    )


@router.post(
    "/stocktakes/{stocktake_id}/items",
    status_code=status.HTTP_201_CREATED,
    response_model=Envelope[StocktakeResponse],
    summary="Record one counted line; recounting a line replaces it",
)
async def upsert_stocktake_line(
    stocktake_id: UUID,
    payload: StocktakeLineRequest,
    session: SessionDep,
    context: StoreManagerDep,
    request_id: RequestIdDep,
) -> Envelope[StocktakeResponse]:
    await service.upsert_stocktake_line(
        session,
        context,
        stocktake_id,
        store_product_id=payload.store_product_id,
        counted_quantity=payload.counted_quantity,
    )
    stocktake, lines = await service.stocktake_view(session, context, stocktake_id)
    return Envelope(data=_stocktake_response(stocktake, lines), request_id=request_id)


@router.post(
    "/stocktakes/{stocktake_id}/finalize",
    response_model=Envelope[StocktakeSummaryResponse],
    summary="Book every variance as an adjustment and close the session",
)
async def finalize_stocktake(
    stocktake_id: UUID,
    session: SessionDep,
    context: StoreManagerDep,
    request_id: RequestIdDep,
) -> Envelope[StocktakeSummaryResponse]:
    stocktake, corrected, unchanged = await service.finalize_stocktake(
        session, context, stocktake_id, request_id=request_id
    )
    _stocktake, lines = await service.stocktake_view(session, context, stocktake_id)
    return Envelope(
        data=StocktakeSummaryResponse(
            stocktake=_stocktake_response(stocktake, lines),
            corrected_lines=corrected,
            unchanged_lines=unchanged,
        ),
        request_id=request_id,
    )

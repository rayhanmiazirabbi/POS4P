from __future__ import annotations

from datetime import date, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from app.context import RequestContext
from app.dependencies import (
    ContextDep,
    RequestIdDep,
    SessionDep,
    StoreContextDep,
    require_roles,
)
from app.models import Role
from app.schemas.base import Envelope, Page
from app.schemas.reports import (
    BranchRollupResponse,
    CogsResponse,
    ComparisonResponse,
    DailyMetricResponse,
    DeadStockResponse,
    ExpenseCreateRequest,
    ExpenseResponse,
    ExpiryWarning,
    LowStockItem,
    TodayMetricsResponse,
    TopCustomerRow,
    TopProductRow,
    ValuationResponse,
)
from app.services import reports as service

router = APIRouter(prefix="/reports", tags=["Reports"])

StoreManagerDep = Annotated[RequestContext, Depends(require_roles(Role.OWNER, Role.MANAGER))]

#: Every report is cut on one branch's trading day, so a caller with no store in
#: context has no answerable question -- reject it rather than guess a branch.
AsOfQuery = Annotated[
    datetime | None,
    Query(alias="asOf", description="Instant selecting the trading day; defaults to now."),
]


@router.get("/today", response_model=Envelope[TodayMetricsResponse])
async def read_today_metrics(
    session: SessionDep,
    context: StoreContextDep,
    request_id: RequestIdDep,
    as_of: AsOfQuery = None,
) -> Envelope[TodayMetricsResponse]:
    """Trading-day metrics for the caller's store; profit is redacted by role."""
    metrics = await service.today_metrics(session, context, as_of=as_of)
    if not service.can_see_profit(context):
        metrics.profit = None
    return Envelope(data=metrics, request_id=request_id)


@router.post(
    "/daily-metrics/rebuild",
    response_model=Envelope[DailyMetricResponse],
    summary="Rebuild the daily metric projection from the ledgers (owner/manager only)",
)
async def rebuild_daily_metric(
    session: SessionDep,
    context: StoreManagerDep,
    request_id: RequestIdDep,
    as_of: AsOfQuery = None,
) -> Envelope[DailyMetricResponse]:
    metric = await service.rebuild_daily_metric(
        session, context, as_of=as_of, request_id=request_id
    )
    return Envelope(data=metric, request_id=request_id)


@router.get("/low-stock", response_model=Envelope[list[LowStockItem]])
async def list_low_stock(
    session: SessionDep,
    context: StoreContextDep,
    request_id: RequestIdDep,
) -> Envelope[list[LowStockItem]]:
    """Products below their branch minimum for the caller's store."""
    items = await service.low_stock(session, context)
    return Envelope(data=items, request_id=request_id)


@router.get("/expiry", response_model=Envelope[list[ExpiryWarning]])
async def list_expiry_warnings(
    session: SessionDep,
    context: StoreContextDep,
    request_id: RequestIdDep,
    within_days: Annotated[int, Query(alias="withinDays", ge=0, le=365)] = 30,
) -> Envelope[list[ExpiryWarning]]:
    """Batches expiring within ``within_days`` for the caller's store."""
    items = await service.expiry_warnings(session, context, within_days=within_days)
    return Envelope(data=items, request_id=request_id)


@router.get("/expenses", response_model=Envelope[Page[ExpenseResponse]])
async def list_expenses(
    session: SessionDep,
    context: StoreContextDep,
    request_id: RequestIdDep,
    date_from: Annotated[date | None, Query(alias="from")] = None,
    date_to: Annotated[date | None, Query(alias="to")] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Envelope[Page[ExpenseResponse]]:
    """Expenses recorded on the caller's store, newest first."""
    rows, total = await service.list_expenses(
        session, context, date_from=date_from, date_to=date_to, limit=limit, offset=offset
    )
    return Envelope(
        data=Page(
            items=[ExpenseResponse.model_validate(expense) for expense in rows],
            total=total,
        ),
        request_id=request_id,
    )


@router.post(
    "/expenses",
    status_code=status.HTTP_201_CREATED,
    response_model=Envelope[ExpenseResponse],
    summary="Record a branch expense (owner/manager only)",
)
async def create_expense(
    payload: ExpenseCreateRequest,
    session: SessionDep,
    context: StoreManagerDep,
    request_id: RequestIdDep,
) -> Envelope[ExpenseResponse]:
    expense = await service.create_expense(session, context, payload, request_id=request_id)
    return Envelope(data=ExpenseResponse.model_validate(expense), request_id=request_id)


@router.get("/comparison", response_model=Envelope[ComparisonResponse])
async def read_sales_comparison(
    session: SessionDep,
    context: StoreContextDep,
    request_id: RequestIdDep,
    as_of: AsOfQuery = None,
) -> Envelope[ComparisonResponse]:
    """Today versus yesterday on branch time; profit redacted by role."""
    result = await service.sales_comparison(session, context, as_of=as_of)
    if not service.can_see_profit(context):
        result.current.profit = None
        result.previous.profit = None
    return Envelope(data=result, request_id=request_id)


@router.get("/branch-rollup", response_model=Envelope[BranchRollupResponse])
async def read_branch_rollup(
    session: SessionDep,
    context: StoreManagerDep,
    request_id: RequestIdDep,
    as_of: AsOfQuery = None,
) -> Envelope[BranchRollupResponse]:
    """Organization-wide trading-day rollup per branch (owner/manager only)."""
    result = await service.branch_rollup(session, context, as_of=as_of)
    return Envelope(data=result, request_id=request_id)


@router.get("/top-products", response_model=Envelope[list[TopProductRow]])
async def read_top_products(
    session: SessionDep,
    context: ContextDep,
    request_id: RequestIdDep,
    date_from: Annotated[datetime | None, Query(alias="from")] = None,
    date_to: Annotated[datetime | None, Query(alias="to")] = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 10,
) -> Envelope[list[TopProductRow]]:
    """Best sellers by revenue across the organization in an optional window."""
    rows = await service.top_products(
        session, context, start=date_from, end=date_to, limit=limit
    )
    return Envelope(data=rows, request_id=request_id)


@router.get("/top-customers", response_model=Envelope[list[TopCustomerRow]])
async def read_top_customers(
    session: SessionDep,
    context: StoreManagerDep,
    request_id: RequestIdDep,
    date_from: Annotated[datetime | None, Query(alias="from")] = None,
    date_to: Annotated[datetime | None, Query(alias="to")] = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 10,
) -> Envelope[list[TopCustomerRow]]:
    """Highest-spending identified customers (owner/manager only)."""
    rows = await service.top_customers(
        session, context, start=date_from, end=date_to, limit=limit
    )
    return Envelope(data=rows, request_id=request_id)


@router.get("/inventory-valuation", response_model=Envelope[ValuationResponse])
async def read_inventory_valuation(
    session: SessionDep,
    context: StoreManagerDep,
    request_id: RequestIdDep,
) -> Envelope[ValuationResponse]:
    """Cost-basis value of the caller's shelf, per product (owner/manager only)."""
    result = await service.inventory_valuation(session, context)
    return Envelope(data=result, request_id=request_id)


@router.get("/dead-stock", response_model=Envelope[DeadStockResponse])
async def read_dead_stock(
    session: SessionDep,
    context: StoreManagerDep,
    request_id: RequestIdDep,
    idle_days: Annotated[int, Query(alias="idleDays", ge=1, le=730)] = 90,
) -> Envelope[DeadStockResponse]:
    """Held stock with no sale movement in ``idleDays`` (owner/manager only)."""
    result = await service.dead_stock(session, context, idle_days=idle_days)
    return Envelope(data=result, request_id=request_id)


@router.get("/cogs", response_model=Envelope[CogsResponse])
async def read_cogs(
    session: SessionDep,
    context: StoreManagerDep,
    request_id: RequestIdDep,
    date_from: Annotated[datetime, Query(alias="from")],
    date_to: Annotated[datetime, Query(alias="to")],
) -> Envelope[CogsResponse]:
    """Batch cost of goods sold in the window, net of sale-return restocks."""
    result = await service.windowed_cogs(session, context, start=date_from, end=date_to)
    return Envelope(data=result, request_id=request_id)

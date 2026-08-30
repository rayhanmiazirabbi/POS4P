from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.context import RequestContext
from app.domains.customers import Customer
from app.domains.inventory import InventoryBatch, InventoryMovement, InventoryMovementType
from app.domains.payments import Payment, PaymentMethod, PaymentRefund, PaymentStatus
from app.domains.products import PharmacyProduct
from app.domains.reports import DailyStoreMetric, StoreExpense
from app.domains.sales import Sale, SaleItem, SaleItemBatchAllocation, SaleReturn, SaleStatus
from app.errors import Forbidden, ValidationError
from app.models import Role, Store, StoreProduct
from app.schemas.reports import (
    BranchRollupResponse,
    BranchRollupRow,
    ComparisonResponse,
    CogsResponse,
    DailyMetricResponse,
    DeadStockLine,
    DeadStockResponse,
    ExpenseCreateRequest,
    ExpiryWarning,
    LowStockItem,
    TodayMetricsResponse,
    TopCustomerRow,
    TopProductRow,
    ValuationLine,
    ValuationResponse,
)
from app.security import utc_now
from app.services.audit import record_audit, redact
from app.services.inventory import expiring_batches, low_stock_products
from app.services.stores import business_date, load_current_store, store_settings_of

#: Cost visibility is an owner/manager capability; everyone else gets redaction.
PROFIT_ROLES = frozenset({Role.OWNER, Role.MANAGER})

CENT = Decimal("0.01")

#: A ``due`` payment records a receivable, not money in the drawer. It belongs in
#: the breakdown so the day is explainable, but never in the collected total.
#: Everything else -- built-in cash, named wallets, tenant-configured methods --
#: crossed the counter, so collected is "any method but due" rather than a fixed
#: list that would silently exclude a method the tenant added later.
DUE_METHOD = PaymentMethod.DUE.value


def collected_from(breakdown: dict[str, Decimal]) -> Decimal:
    """Net cash and wallet movement in a payment breakdown, excluding credit.

    Shared by the live rollup and the rebuilt projection so the two cannot disagree
    about what the till should hold -- integration check 6 requires that a rebuild
    reproduce the incremental figure, and two copies of this sum would not.
    """
    return sum(
        (amount for method, amount in breakdown.items() if method != DUE_METHOD),
        Decimal(0),
    ).quantize(CENT)


def can_see_profit(context: RequestContext) -> bool:
    return context.role in PROFIT_ROLES


def _money(value: object) -> Decimal:
    """A summed money column as a Decimal, tolerating the ``NULL`` of an empty set.

    ``coalesce`` covers the aggregate itself, but a ``scalar()`` is still typed
    optional, and SQLite hands sums back as floats -- so route both through ``str``
    rather than letting binary floating point into a money figure.
    """
    if value is None:
        return Decimal(0)
    return value if isinstance(value, Decimal) else Decimal(str(value))


def _day_window(store: Store, *, moment: datetime | None = None) -> tuple[datetime, datetime, date]:
    """UTC half-open window of the store's trading day, plus the day it represents.

    The window is anchored on the branch's own calendar and cutoff hour -- a 2am
    sale belongs to the shift that started the previous evening, and a store on
    ``Asia/Dhaka`` closes its books six hours before UTC does.

    Returned naive because SQLite (the test backend) compares DATETIME text, so
    every timestamp touching this predicate must share one representation.
    """
    tz = ZoneInfo(store.timezone)
    trading_day = business_date(store, moment=moment)
    cutoff = store_settings_of(store).business_day_cutoff_hour
    start_local = datetime(
        trading_day.year, trading_day.month, trading_day.day, tzinfo=tz
    ) + timedelta(hours=cutoff)
    start_utc = start_local.astimezone(ZoneInfo("UTC")).replace(tzinfo=None)
    return start_utc, start_utc + timedelta(days=1), trading_day


async def _gross_sales(
    session: AsyncSession, context: RequestContext, store: Store, start: datetime, end: datetime
) -> tuple[Decimal, int]:
    """Completed gross sales and their transaction count inside the window."""
    scope = (
        Sale.organization_id == context.organization_id,
        Sale.store_id == store.id,
        Sale.status == SaleStatus.COMPLETED,
        Sale.created_at >= start,
        Sale.created_at < end,
    )
    total = _money(await session.scalar(select(func.coalesce(func.sum(Sale.total), 0)).where(*scope)))
    count = int(await session.scalar(select(func.count()).select_from(Sale).where(*scope)))
    return total.quantize(CENT), count


async def _refund_total(
    session: AsyncSession, context: RequestContext, store: Store, start: datetime, end: datetime
) -> Decimal:
    """Returns booked in the window, as a positive figure.

    ``SaleReturn.total`` is stored negative so the ledger sums to net; reports read
    better with a positive refund line, so the sign is flipped once, here.
    """
    total = _money(
        await session.scalar(
            select(func.coalesce(func.sum(SaleReturn.total), 0)).where(
                SaleReturn.organization_id == context.organization_id,
                SaleReturn.store_id == store.id,
                SaleReturn.created_at >= start,
                SaleReturn.created_at < end,
            )
        )
    )
    return abs(total).quantize(CENT)


async def _payment_breakdown(
    session: AsyncSession, context: RequestContext, store: Store, start: datetime, end: datetime
) -> dict[str, Decimal]:
    """Net money movement per tender inside the window.

    This answers a different question from ``sales_total``, and deliberately so.
    Revenue is recognised against the *sale*; cash is counted against the *movement*.
    So both halves here key on their own timestamp and ignore the sale's status:

    * A sale voided the same day took cash in and handed it back, netting to zero --
      counting only the refund would report a shortfall the drawer never had.
    * A refund paid out today against yesterday's sale is money leaving today's
      drawer, so it belongs to today even though the revenue belonged to yesterday.

    A line can therefore be negative, which is why the response type is signed: a
    quiet morning spent refunding a large sale from last night really does leave the
    till down. Clamping that at zero is what makes a till stop reconciling.
    """
    tender_scope = (
        Payment.organization_id == context.organization_id,
        Payment.store_id == store.id,
        Payment.reference_type == "sale",
        Payment.status == PaymentStatus.CAPTURED,
    )
    rows = await session.execute(
        select(Payment.method, func.sum(Payment.amount))
        .where(*tender_scope, Payment.created_at >= start, Payment.created_at < end)
        .group_by(Payment.method)
    )
    breakdown = {_method_key(method): _money(total) for method, total in rows.all()}

    refund_rows = await session.execute(
        select(Payment.method, func.sum(PaymentRefund.amount))
        .join(Payment, Payment.id == PaymentRefund.payment_id)
        .where(
            *tender_scope,
            PaymentRefund.created_at >= start,
            PaymentRefund.created_at < end,
        )
        .group_by(Payment.method)
    )
    for method, refunded in refund_rows.all():
        key = _method_key(method)
        breakdown[key] = breakdown.get(key, Decimal(0)) - _money(refunded)

    return {
        method: amount.quantize(CENT) for method, amount in breakdown.items() if amount != 0
    }


def _method_key(method: PaymentMethod | str) -> str:
    """``use_enum_values`` means a method can arrive as an enum or a raw string."""
    return method.value if isinstance(method, PaymentMethod) else str(method)


async def _expense_total(
    session: AsyncSession, context: RequestContext, store: Store, trading_day: date
) -> Decimal:
    """Expenses dated to the trading day; they are recorded per date, not per instant."""
    total = _money(
        await session.scalar(
            select(func.coalesce(func.sum(StoreExpense.amount), 0)).where(
                StoreExpense.organization_id == context.organization_id,
                StoreExpense.store_id == store.id,
                StoreExpense.expense_date == trading_day,
            )
        )
    )
    return total.quantize(CENT)


async def _cost_of_goods_sold(
    session: AsyncSession, context: RequestContext, store: Store, start: datetime, end: datetime
) -> Decimal:
    """Batch cost of everything sold in the window, net of stock returned into it.

    Cost is summed per *allocation*, because one sale line can be filled FEFO from
    several batches at different unit costs. Joining the line total through the
    allocations instead would repeat that revenue once per batch.

    Returns restore stock through ``RETURN`` movements that carry their own batch,
    so the cost that came back is read off the ledger rather than estimated. Without
    it, netting refund revenue out of profit would book the loss twice.
    """
    sold = _money(
        await session.scalar(
            select(
                func.coalesce(
                    func.sum(SaleItemBatchAllocation.quantity * InventoryBatch.unit_cost), 0
                )
            )
            .select_from(SaleItemBatchAllocation)
            .join(InventoryBatch, InventoryBatch.id == SaleItemBatchAllocation.batch_id)
            .join(SaleItem, SaleItem.id == SaleItemBatchAllocation.sale_item_id)
            .join(Sale, Sale.id == SaleItem.sale_id)
            .where(
                Sale.organization_id == context.organization_id,
                Sale.store_id == store.id,
                Sale.status == SaleStatus.COMPLETED,
                Sale.created_at >= start,
                Sale.created_at < end,
            )
        )
    )
    restored = _money(
        await session.scalar(
            select(
                func.coalesce(
                    func.sum(func.abs(InventoryMovement.quantity) * InventoryBatch.unit_cost), 0
                )
            )
            .select_from(InventoryMovement)
            .join(InventoryBatch, InventoryBatch.id == InventoryMovement.batch_id)
            .where(
                InventoryMovement.organization_id == context.organization_id,
                InventoryMovement.store_id == store.id,
                InventoryMovement.movement_type == InventoryMovementType.RETURN,
                InventoryMovement.reference_type == "sale_return",
                InventoryMovement.occurred_at >= start,
                InventoryMovement.occurred_at < end,
            )
        )
    )
    return (sold - restored).quantize(CENT)


async def today_metrics(
    session: AsyncSession, context: RequestContext, *, as_of: datetime | None = None
) -> TodayMetricsResponse:
    """Sales, refunds, payment mix, expenses, and profit for one trading day.

    ``as_of`` selects the day explicitly, which is how a branch closes yesterday's
    books after midnight. Profit is computed from batch allocations and is ``None``
    unless the caller owns cost visibility.
    """
    store = await load_current_store(session, context)
    start, end, trading_day = _day_window(store, moment=as_of)

    sales_total, transaction_count = await _gross_sales(session, context, store, start, end)
    refund_total = await _refund_total(session, context, store, start, end)
    breakdown = await _payment_breakdown(session, context, store, start, end)
    expense_total = await _expense_total(session, context, store, trading_day)

    due_total = breakdown.get(PaymentMethod.DUE.value, Decimal(0))

    profit: Decimal | None = None
    if can_see_profit(context):
        cost_total = await _cost_of_goods_sold(session, context, store, start, end)
        profit = (sales_total - refund_total - cost_total).quantize(CENT)

    return TodayMetricsResponse(
        business_date=trading_day,
        sales_total=sales_total,
        refund_total=refund_total,
        net_sales_total=(sales_total - refund_total).quantize(CENT),
        transaction_count=transaction_count,
        payment_breakdown=breakdown,
        collected_total=collected_from(breakdown),
        due_total=due_total,
        expense_total=expense_total,
        profit=profit,
        as_of=as_of or utc_now(),
    )


async def rebuild_daily_metric(
    session: AsyncSession,
    context: RequestContext,
    *,
    as_of: datetime | None = None,
    request_id: str = "unknown",
) -> DailyMetricResponse:
    """Recompute the ``daily_store_metrics`` projection for one trading day.

    The row is a rebuildable read model over the sale and payment ledgers, so this
    upserts rather than appends: rebuilding twice must leave one row that agrees
    with what ``today_metrics`` computes live.
    """
    store = await load_current_store(session, context)
    start, end, trading_day = _day_window(store, moment=as_of)

    sales_total, _ = await _gross_sales(session, context, store, start, end)
    refund_total = await _refund_total(session, context, store, start, end)
    breakdown = await _payment_breakdown(session, context, store, start, end)
    cost_total = await _cost_of_goods_sold(session, context, store, start, end)

    metric = await session.scalar(
        select(DailyStoreMetric).where(
            DailyStoreMetric.organization_id == context.organization_id,
            DailyStoreMetric.store_id == store.id,
            DailyStoreMetric.metric_date == trading_day,
        )
    )
    if metric is None:
        metric = DailyStoreMetric(
            organization_id=context.organization_id,
            store_id=store.id,
            metric_date=trading_day,
        )
        session.add(metric)

    metric.sales_total = sales_total
    metric.refund_total = refund_total
    metric.cost_total = cost_total
    metric.payment_breakdown = {method: str(amount) for method, amount in breakdown.items()}
    metric.rebuilt_at = utc_now()

    record_audit(
        session,
        context,
        action="reports.daily_metric_rebuilt",
        entity_type="daily_store_metric",
        entity_id=metric.id,
        request_id=request_id,
        after=redact({"metric_date": str(trading_day), "sales_total": str(sales_total)}),
    )
    await session.commit()
    await session.refresh(metric)

    return DailyMetricResponse(
        store_id=metric.store_id,
        metric_date=metric.metric_date,
        sales_total=Decimal(metric.sales_total),
        refund_total=Decimal(metric.refund_total),
        cost_total=Decimal(metric.cost_total),
        payment_breakdown={
            method: Decimal(amount) for method, amount in metric.payment_breakdown.items()
        },
        collected_total=collected_from(
            {method: Decimal(amount) for method, amount in metric.payment_breakdown.items()}
        ),
        rebuilt_at=metric.rebuilt_at,
    )


async def create_expense(
    session: AsyncSession,
    context: RequestContext,
    payload: ExpenseCreateRequest,
    *,
    request_id: str = "unknown",
) -> StoreExpense:
    """Record a branch expense; owner/manager only (enforced at the router too)."""
    store = await load_current_store(session, context)
    expense = StoreExpense(
        organization_id=context.organization_id,
        store_id=store.id,
        category=payload.category.strip(),
        amount=payload.amount,
        expense_date=payload.expense_date,
        note=payload.note,
        created_by_user_id=context.user_id,
    )
    session.add(expense)
    record_audit(
        session,
        context,
        action="reports.expense_created",
        entity_type="store_expense",
        entity_id=expense.id,
        request_id=request_id,
        after=redact({"category": expense.category, "amount": str(expense.amount)}),
    )
    await session.commit()
    return expense


async def list_expenses(
    session: AsyncSession,
    context: RequestContext,
    *,
    date_from: date | None = None,
    date_to: date | None = None,
    limit: int = 25,
    offset: int = 0,
) -> tuple[list[StoreExpense], int]:
    """A page of the caller's branch expenses, newest first, with the total count."""
    store = await load_current_store(session, context)
    scope = [
        StoreExpense.organization_id == context.organization_id,
        StoreExpense.store_id == store.id,
    ]
    if date_from is not None:
        scope.append(StoreExpense.expense_date >= date_from)
    if date_to is not None:
        scope.append(StoreExpense.expense_date <= date_to)

    total = int(await session.scalar(select(func.count()).select_from(StoreExpense).where(*scope)))
    rows = list(
        await session.scalars(
            select(StoreExpense)
            .where(*scope)
            .order_by(StoreExpense.expense_date.desc(), StoreExpense.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
    )
    return rows, total


async def low_stock(session: AsyncSession, context: RequestContext) -> list[LowStockItem]:
    """Products below their branch minimum for the current store."""
    store = await load_current_store(session, context)
    rows = await low_stock_products(session, context, store.id)
    names = await _product_names(session, [product for product, _ in rows])
    return [
        LowStockItem(
            store_product_id=product.id,
            sku=product.sku,
            product_name=names.get(product.id, product.sku),
            available=available,
            minimum_stock=Decimal(product.minimum_stock),
        )
        for product, available in rows
    ]


async def expiry_warnings(
    session: AsyncSession, context: RequestContext, *, within_days: int = 30
) -> list[ExpiryWarning]:
    """Batches expiring within the window for the current store."""
    store = await load_current_store(session, context)
    rows = await expiring_batches(session, context, store.id, within_days=within_days)
    names = await _product_names(session, [product for product, _, _ in rows])
    # Counted from the branch's trading day, matching the window the batches were
    # selected with -- a UTC anchor here would report 11 days for a 10-day window.
    today = business_date(store)
    return [
        ExpiryWarning(
            store_product_id=product.id,
            sku=product.sku,
            product_name=names.get(product.id, product.sku),
            batch_id=batch.id,
            batch_number=batch.batch_number,
            expiry_date=batch.expiry_date,
            available=available,
            days_until_expiry=(batch.expiry_date - today).days,
        )
        for product, batch, available in rows
        if batch.expiry_date is not None
    ]


async def _product_names(
    session: AsyncSession, store_products: list[StoreProduct]
) -> dict[UUID, str]:
    """Map store product ids to their catalog names, falling back to the SKU."""
    ids = {sp.id for sp in store_products}
    if not ids:
        return {}
    rows = await session.execute(
        select(StoreProduct.id, PharmacyProduct.name)
        .join(PharmacyProduct, PharmacyProduct.id == StoreProduct.pharmacy_product_id)
        .where(StoreProduct.id.in_(ids))
    )
    return {store_product_id: name or store_product_id for store_product_id, name in rows.all()}


# --- stage 2: comparisons, rollups, and analysis ------------------------------


async def period_metrics(
    session: AsyncSession,
    context: RequestContext,
    store: Store,
    start: datetime,
    end: datetime,
) -> TodayMetricsResponse:
    """The today-metrics computation over an explicit instant window.

    Comparisons reuse the exact same aggregation as the daily view so a period can
    never disagree with what ``/reports/today`` would have said about its days.
    """
    sales_total, transaction_count = await _gross_sales(session, context, store, start, end)
    refund_total = await _refund_total(session, context, store, start, end)
    breakdown = await _payment_breakdown(session, context, store, start, end)
    profit: Decimal | None = None
    if can_see_profit(context):
        cost_total = await _cost_of_goods_sold(session, context, store, start, end)
        profit = (sales_total - refund_total - cost_total).quantize(CENT)
    return TodayMetricsResponse(
        business_date=start.date(),
        sales_total=sales_total,
        refund_total=refund_total,
        net_sales_total=(sales_total - refund_total).quantize(CENT),
        transaction_count=transaction_count,
        payment_breakdown=breakdown,
        collected_total=collected_from(breakdown),
        due_total=breakdown.get(PaymentMethod.DUE.value, Decimal(0)),
        expense_total=Decimal(0),
        profit=profit,
        as_of=start,
    )


def previous_window(start: datetime, end: datetime) -> tuple[datetime, datetime]:
    """The window of equal length immediately before ``[start, end)``."""
    length = end - start
    return start - length, end - length


async def sales_comparison(
    session: AsyncSession, context: RequestContext, *, as_of: datetime | None = None
) -> ComparisonResponse:
    """This trading day versus the trading day before it, on branch time."""
    store = await load_current_store(session, context)
    start, end, _day = _day_window(store, moment=as_of)
    prev_start, prev_end = previous_window(start, end)
    current = await period_metrics(session, context, store, start, end)
    previous = await period_metrics(session, context, store, prev_start, prev_end)
    if previous.sales_total == 0:
        change = Decimal("100.00") if current.sales_total > 0 else Decimal("0.00")
    else:
        change = (
            (current.sales_total - previous.sales_total)
            / previous.sales_total
            * Decimal(100)
        ).quantize(Decimal("0.01"))
    return ComparisonResponse(current=current, previous=previous, sales_change=change)


async def branch_rollup(
    session: AsyncSession,
    context: RequestContext,
    *,
    as_of: datetime | None = None,
) -> BranchRollupResponse:
    """One organization-wide trading day, per branch; owner/manager only."""
    if not can_see_profit(context):
        raise Forbidden("Branch rollup requires owner or manager")
    stores = list(
        await session.scalars(
            select(Store).where(Store.organization_id == context.organization_id).order_by(Store.code)
        )
    )
    rows: list[BranchRollupRow] = []
    business_day = business_date(stores[0]) if stores else utc_now().date()
    for store in stores:
        start, end, day = _day_window(store, moment=as_of)
        business_day = day
        sales_total, count = await _gross_sales(session, context, store, start, end)
        refund_total = await _refund_total(session, context, store, start, end)
        rows.append(
            BranchRollupRow(
                store_id=store.id,
                store_name=store.name,
                business_date=day,
                sales_total=sales_total,
                refund_total=refund_total,
                net_sales_total=(sales_total - refund_total).quantize(CENT),
                transaction_count=count,
            )
        )
    return BranchRollupResponse(
        business_date=business_day,
        rows=rows,
        total_sales=sum((r.sales_total for r in rows), Decimal(0)).quantize(CENT),
        total_transactions=sum(r.transaction_count for r in rows),
    )


async def top_products(
    session: AsyncSession,
    context: RequestContext,
    *,
    start: datetime | None = None,
    end: datetime | None = None,
    limit: int = 10,
) -> list[TopProductRow]:
    """Best sellers by revenue inside an optional window across all branches.

    Revenue is line revenue net of nothing -- refunds land as their own negative
    lines in a fuller export, but the ranking question is "what sells", which net
    figures answer worse.
    """
    if limit < 1 or limit > 50:
        raise ValidationError("limit must be between 1 and 50")
    scope = [
        Sale.organization_id == context.organization_id,
        Sale.status == SaleStatus.COMPLETED,
    ]
    if start is not None:
        scope.append(Sale.created_at >= start)
    if end is not None:
        scope.append(Sale.created_at < end)
    rows = await session.execute(
        select(
            SaleItem.store_product_id,
            SaleItem.product_name,
            func.sum(SaleItem.quantity),
            func.sum(SaleItem.line_total),
        )
        .select_from(SaleItem)
        .join(Sale, Sale.id == SaleItem.sale_id)
        .where(*scope)
        .group_by(SaleItem.store_product_id, SaleItem.product_name)
        .order_by(func.sum(SaleItem.line_total).desc())
        .limit(limit)
    )
    return [
        TopProductRow(
            store_product_id=product_id,
            product_name=name or str(product_id),
            quantity_sold=_money(qty),
            revenue=_money(revenue).quantize(CENT),
        )
        for product_id, name, qty, revenue in rows.all()
    ]


async def top_customers(
    session: AsyncSession,
    context: RequestContext,
    *,
    start: datetime | None = None,
    end: datetime | None = None,
    limit: int = 10,
) -> list[TopCustomerRow]:
    """Highest-spending identified customers across branches; owner/manager only."""
    if not can_see_profit(context):
        raise Forbidden("Customer analysis requires owner or manager")
    if limit < 1 or limit > 50:
        raise ValidationError("limit must be between 1 and 50")
    scope = [
        Sale.organization_id == context.organization_id,
        Sale.status == SaleStatus.COMPLETED,
        Sale.customer_id.is_not(None),
    ]
    if start is not None:
        scope.append(Sale.created_at >= start)
    if end is not None:
        scope.append(Sale.created_at < end)
    rows = await session.execute(
        select(
            Sale.customer_id,
            func.coalesce(Customer.name, ""),
            func.count(),
            func.sum(Sale.total),
        )
        .select_from(Sale)
        .outerjoin(Customer, Customer.id == Sale.customer_id)
        .where(*scope)
        .group_by(Sale.customer_id, Customer.name)
        .order_by(func.sum(Sale.total).desc())
        .limit(limit)
    )
    result_rows = rows.all()
    return [
        TopCustomerRow(
            customer_id=customer_id,
            customer_name=name or f"Customer {customer_id}",
            sale_count=int(count),
            total_spent=_money(spent).quantize(CENT),
        )
        for customer_id, name, count, spent in result_rows
    ]


# --- stage 3: stock valuation, dead stock, windowed COGS -----------------------


async def _stock_at_cost(session: AsyncSession, store: Store) -> dict[UUID, tuple[Decimal, Decimal]]:
    """Per product: (quantity held, cost value of that quantity), batch-sourced.

    Valuation follows the same source of truth as FEFO allocation -- movement
    sums per batch -- so a valuation can never disagree with what the shelf
    would actually hand a customer. Batchless corrections (damage, counts) are
    invisible here by design: a unit with no batch has no cost attached to it.
    Costless opening stock is valued at zero, which is what was paid.
    """
    rows = await session.execute(
        select(InventoryMovement.batch_id, func.sum(InventoryMovement.quantity))
        .where(
            InventoryMovement.store_id == store.id,
            InventoryMovement.batch_id.is_not(None),
        )
        .group_by(InventoryMovement.batch_id)
    )
    on_hand = {batch_id: _money(total) for batch_id, total in rows.all()}
    per_product: dict[UUID, tuple[Decimal, Decimal]] = {}
    for batch in await session.scalars(
        select(InventoryBatch).where(InventoryBatch.store_id == store.id)
    ):
        quantity = on_hand.get(batch.id, Decimal(0))
        if quantity <= 0:
            continue
        held, value = per_product.get(batch.store_product_id, (Decimal(0), Decimal(0)))
        per_product[batch.store_product_id] = (
            held + quantity,
            value + (quantity * Decimal(batch.unit_cost)).quantize(CENT),
        )
    return per_product


async def inventory_valuation(
    session: AsyncSession, context: RequestContext
) -> ValuationResponse:
    """Cost-basis value of everything on the caller's shelves (owner/manager)."""
    if not can_see_profit(context):
        raise Forbidden("Valuation requires owner or manager")
    store = await load_current_store(session, context)
    stock = await _stock_at_cost(session, store)
    products = list(
        await session.scalars(
            select(StoreProduct).where(
                StoreProduct.store_id == store.id, StoreProduct.active.is_(True)
            ).order_by(StoreProduct.sku)
        )
    )
    names = await _product_names(session, products)
    lines = [
        ValuationLine(
            store_product_id=product.id,
            sku=product.sku,
            product_name=names.get(product.id, product.sku),
            rack=product.rack,
            on_hand=stock.get(product.id, (Decimal(0), Decimal(0)))[0],
            value_at_cost=stock.get(product.id, (Decimal(0), Decimal(0)))[1],
        )
        for product in products
        if product.id in stock
    ]
    return ValuationResponse(
        store_id=store.id,
        total_value_at_cost=sum((line.value_at_cost for line in lines), Decimal(0)).quantize(CENT),
        lines=lines,
    )


async def dead_stock(
    session: AsyncSession, context: RequestContext, *, idle_days: int = 90
) -> DeadStockResponse:
    """Held stock with no sale movement in ``idle_days`` (owner/manager).

    Cost sits in the shelves doing nothing; the list is what an owner trims or
    returns to the supplier. "Never sold" counts as dead from the day it arrived.
    """
    if not can_see_profit(context):
        raise Forbidden("Dead stock report requires owner or manager")
    if idle_days < 0:
        raise ValidationError("idle_days must be non-negative")
    store = await load_current_store(session, context)
    cutoff = utc_now() - timedelta(days=idle_days)
    last_sold_rows = await session.execute(
        select(InventoryMovement.store_product_id, func.max(InventoryMovement.occurred_at))
        .where(
            InventoryMovement.store_id == store.id,
            InventoryMovement.movement_type == InventoryMovementType.SALE,
        )
        .group_by(InventoryMovement.store_product_id)
    )
    last_sold = dict(last_sold_rows.all())
    # SQLite hands stored-UTC datetimes back naive, PostgreSQL aware; the cutoff
    # is aware, so pin every comparison to aware-UTC regardless of driver.
    from datetime import UTC as _UTC

    def _aware(moment: datetime | None) -> datetime | None:
        return moment.replace(tzinfo=_UTC) if moment is not None and moment.tzinfo is None else moment

    stock = await _stock_at_cost(session, store)
    candidates = [
        product
        for product in await session.scalars(
            select(StoreProduct).where(
                StoreProduct.store_id == store.id, StoreProduct.active.is_(True)
            ).order_by(StoreProduct.sku)
        )
        if stock.get(product.id, (Decimal(0), Decimal(0)))[0] > 0
        and (_aware(last_sold.get(product.id)) is None or _aware(last_sold[product.id]) < cutoff)
    ]
    names = await _product_names(session, candidates)
    lines = [
        DeadStockLine(
            store_product_id=product.id,
            sku=product.sku,
            product_name=names.get(product.id, product.sku),
            on_hand=stock.get(product.id, (Decimal(0), Decimal(0)))[0],
            value_at_cost=stock.get(product.id, (Decimal(0), Decimal(0)))[1],
            last_sold_at=last_sold.get(product.id),
        )
        for product in candidates
    ]
    return DeadStockResponse(
        store_id=store.id,
        idle_days=idle_days,
        total_value_at_cost=sum((line.value_at_cost for line in lines), Decimal(0)).quantize(CENT),
        lines=lines,
    )


async def windowed_cogs(
    session: AsyncSession,
    context: RequestContext,
    *,
    start: datetime,
    end: datetime,
) -> CogsResponse:
    """Batch cost of goods sold across an arbitrary window (owner/manager).

    Reuses the today-metrics cost query so the windowed figure and the daily
    profit figure come from the same code path and cannot drift apart.
    """
    if not can_see_profit(context):
        raise Forbidden("Cost of goods sold requires owner or manager")
    store = await load_current_store(session, context)
    total = await _cost_of_goods_sold(session, context, store, start, end)
    return CogsResponse(store_id=store.id, start=start, end=end, cost_of_goods_sold=total)

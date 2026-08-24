from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated
from uuid import UUID

from pydantic import Field

from app.schemas.base import ApiModel

Money = Annotated[Decimal, Field(ge=0, decimal_places=2)]
#: Profit and net sales legitimately go negative on a bad day; clamping them at
#: zero would silently report a loss as break-even. So does a payment line: a
#: morning spent refunding last night's big sale takes more cash out of the drawer
#: than it puts in, and a report that cannot say so is a report the till will
#: never reconcile against.
SignedMoney = Annotated[Decimal, Field(decimal_places=2)]


class TodayMetricsResponse(ApiModel):
    """Live rollup of a store-local trading day; profit is redacted by role.

    ``sales_total`` is gross completed sales, recognised against the sale. The
    payment figures answer a different question -- what moved through the drawer --
    and so are counted against the payment and refund timestamps instead. The two
    diverge whenever a refund is paid out on a later day than the sale it reverses,
    which is why ``collected_total`` is not derivable from ``net_sales_total``.

    ``due_total`` is the receivable taken on today, not money the till holds.
    """

    business_date: date
    sales_total: Money
    refund_total: Money
    net_sales_total: SignedMoney
    transaction_count: int
    payment_breakdown: dict[str, SignedMoney]
    collected_total: SignedMoney
    due_total: SignedMoney
    expense_total: Money
    profit: SignedMoney | None = None
    as_of: datetime


class DailyMetricResponse(ApiModel):
    """A rebuilt ``daily_store_metrics`` row, reconciled against the ledgers."""

    store_id: UUID
    metric_date: date
    sales_total: Money
    refund_total: Money
    cost_total: Money
    payment_breakdown: dict[str, SignedMoney]
    collected_total: SignedMoney
    rebuilt_at: datetime


class ExpenseCreateRequest(ApiModel):
    category: Annotated[str, Field(min_length=1, max_length=100)]
    amount: Money
    expense_date: date
    note: Annotated[str | None, Field(max_length=500)] = None


class ExpenseResponse(ApiModel):
    """A recorded branch expense.

    ``expense_date`` is when the money was spent and is caller-supplied, so it can be
    backdated; ``created_at`` is when the row was entered. Both are exposed because
    only the pair reveals an expense booked into a day that was already closed.
    """

    id: UUID
    store_id: UUID
    category: str
    amount: Decimal
    expense_date: date
    note: str | None
    created_by_user_id: UUID | None
    created_at: datetime


class LowStockItem(ApiModel):
    store_product_id: UUID
    sku: str
    product_name: str
    available: Decimal
    minimum_stock: Decimal


class ExpiryWarning(ApiModel):
    store_product_id: UUID
    sku: str
    product_name: str
    batch_id: UUID
    batch_number: str
    expiry_date: date
    available: Decimal
    days_until_expiry: int


class ComparisonResponse(ApiModel):
    current: TodayMetricsResponse
    previous: TodayMetricsResponse
    sales_change: Decimal


class BranchRollupRow(ApiModel):
    store_id: UUID
    store_name: str
    business_date: date
    sales_total: Decimal
    refund_total: Decimal
    net_sales_total: Decimal
    transaction_count: int


class BranchRollupResponse(ApiModel):
    business_date: date
    rows: list[BranchRollupRow]
    total_sales: Decimal
    total_transactions: int


class TopProductRow(ApiModel):
    store_product_id: UUID
    product_name: str
    quantity_sold: Decimal
    revenue: Decimal


class TopCustomerRow(ApiModel):
    customer_id: UUID | None
    customer_name: str
    sale_count: int
    total_spent: Decimal

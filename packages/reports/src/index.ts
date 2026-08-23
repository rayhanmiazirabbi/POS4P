import { add, money, subtract, type MoneyValue } from '@pharmacy/money';
import { can } from '@pharmacy/permissions';
import type { PaymentMethod, Role } from '@pharmacy/types';

export type ReportFilters = { organizationId: string; storeIds?: readonly string[]; from: string; to: string; timezone: string; cursor?: string; limit?: number };
export type SalesReportRow = { storeId: string; gross: MoneyValue; refunds: MoneyValue; due: MoneyValue; cost?: MoneyValue };
export type SalesKpis = { gross: MoneyValue; refunds: MoneyValue; net: MoneyValue; due: MoneyValue; profit?: MoneyValue };
/** Re-exported, not redeclared: this package used to add `card`, which no backend enum accepts. */
export type { PaymentMethod };
export type PaymentBreakdown = Partial<Record<PaymentMethod, MoneyValue>>;
export type PaymentRecord = { method: PaymentMethod; amount: MoneyValue };
export type ExpenseEntry = { id: string; category: string; amount: MoneyValue; spentAt: string };
export type TodaySalesKpi = { gross: MoneyValue; refunds: MoneyValue; net: MoneyValue; transactionCount: number };
export type ProfitKpi = { revenue: MoneyValue; cost: MoneyValue; grossProfit: MoneyValue };
export type DashboardKpis = { sales: TodaySalesKpi; profit?: ProfitKpi; payments: PaymentBreakdown; expenses: MoneyValue };
export type DayRange = { from: string; to: string };
export type LowStockWarning = { productId: string; name?: string; available: number; reorderLevel: number; shortfall: number };
export type ExpiryWarning = { batchId: string; productId: string; lotNumber: string; expiryDate: string; daysUntilExpiry: number; onHand: number };
export type ChartPoint = { label: string; value: number };
export type TableRow = { label: string; value: string };
export type PageLike<T> = { items: readonly T[] };

const MS_PER_DAY = 86_400_000;

function assertIsoInstant(value: string, field: string): void {
  if (!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,3})?(Z|[+-]\d{2}:\d{2})$/.test(value)) throw new Error(`Invalid ${field} timestamp: ${value}`);
}

export function validateReportFilters(filters: ReportFilters): ReportFilters {
  assertIsoInstant(filters.from, 'from');
  assertIsoInstant(filters.to, 'to');
  if (filters.from >= filters.to) throw new Error('Report range must be non-empty');
  if (filters.limit !== undefined && (!Number.isInteger(filters.limit) || filters.limit < 1 || filters.limit > 1000)) throw new Error('Invalid report limit');
  return filters.storeIds ? { ...filters, storeIds: [...filters.storeIds] } : { ...filters };
}

/** Local day boundaries for a calendar date at an explicit fixed UTC offset in
 *  minutes (e.g. 360 for `+06:00`). The range is expressed back in UTC so the
 *  backend filters on instants, never on the caller's wall clock. */
export function dayRangeBounds(date: string, utcOffsetMinutes: number): DayRange {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(date)) throw new Error(`Invalid date: ${date}`);
  const [year = 0, month = 0, day = 0] = date.split('-').map(Number);
  const localStartMs = Date.UTC(year, month - 1, day);
  const offsetMs = utcOffsetMinutes * 60_000;
  return { from: new Date(localStartMs - offsetMs).toISOString(), to: new Date(localStartMs - offsetMs + MS_PER_DAY).toISOString() };
}

/** Rows whose instant falls inside `[from, to)`; boundary-inclusive start,
 *  boundary-exclusive end so adjacent days never double-count a sale. */
export function rowsInRange<T extends { createdAt: string }>(rows: readonly T[], range: DayRange): T[] {
  return rows.filter((row) => row.createdAt >= range.from && row.createdAt < range.to).sort((a, b) => a.createdAt.localeCompare(b.createdAt));
}

export function summarizeSales(rows: readonly SalesReportRow[], includeCosts: boolean): SalesKpis {
  const gross = add(...rows.map((row) => row.gross));
  const refunds = add(...rows.map((row) => row.refunds));
  const net = subtract(gross, refunds);
  const due = add(...rows.map((row) => row.due));
  const result: SalesKpis = { gross, refunds, net, due };
  if (includeCosts) {
    const cost = add(...rows.map((row) => row.cost ?? money('0.00')));
    result.profit = subtract(net, cost);
  }
  return result;
}

/** Sum one page's worth of rows into KPIs; combining per-page results is exact
 *  because every step runs on bigint cents. */
export function summarizePage(page: PageLike<SalesReportRow>, includeCosts: boolean): SalesKpis {
  return summarizeSales(page.items, includeCosts);
}

export function combinePageKpis(pages: readonly SalesKpis[], includeCosts: boolean): SalesKpis {
  const gross = add(...pages.map((page) => page.gross));
  const refunds = add(...pages.map((page) => page.refunds));
  const due = add(...pages.map((page) => page.due));
  const net = subtract(gross, refunds);
  const result: SalesKpis = { gross, refunds, net, due };
  if (includeCosts && pages.every((page) => page.profit !== undefined)) {
    result.profit = add(...pages.map((page) => page.profit as MoneyValue));
  }
  return result;
}

export function paymentBreakdown(payments: readonly PaymentRecord[]): PaymentBreakdown {
  const byMethod = new Map<PaymentMethod, MoneyValue>();
  for (const payment of payments) {
    const current = byMethod.get(payment.method);
    byMethod.set(payment.method, current ? add(current, payment.amount) : payment.amount);
  }
  return Object.fromEntries(byMethod) as PaymentBreakdown;
}

export function expenseTotal(entries: readonly ExpenseEntry[]): MoneyValue {
  return add(...entries.map((entry) => entry.amount));
}

export function todaySalesKpi(rows: readonly SalesReportRow[], transactionCount: number): TodaySalesKpi {
  const kpis = summarizeSales(rows, false);
  return { gross: kpis.gross, refunds: kpis.refunds, net: kpis.net, transactionCount };
}

export function profitKpi(rows: readonly SalesReportRow[]): ProfitKpi {
  const kpis = summarizeSales(rows, true);
  const cost = subtract(kpis.net, kpis.profit ?? kpis.net);
  return { revenue: kpis.net, cost, grossProfit: kpis.profit ?? money('0.00') };
}

export function buildDashboardKpis(input: { salesRows: readonly SalesReportRow[]; payments: readonly PaymentRecord[]; expenses: readonly ExpenseEntry[]; transactionCount: number }, role: Role): DashboardKpis {
  const includeCosts = canReadCosts(role);
  const kpis: DashboardKpis = { sales: todaySalesKpi(input.salesRows, input.transactionCount), payments: paymentBreakdown(input.payments), expenses: expenseTotal(input.expenses) };
  if (includeCosts) kpis.profit = profitKpi(input.salesRows);
  return kpis;
}

/**
 * Whether a role may see cost and profit figures.
 *
 * Delegated to `@pharmacy/permissions` rather than decided here. This package used
 * to keep its own `COST_ROLES` set, so the same question had two answers in the
 * codebase and only one of them was the role matrix -- add a role, or move cost
 * access between roles, and the dashboard would have quietly kept the old answer.
 * The capability, not the role list, is the thing being asked about.
 */
export function canReadCosts(role: Role): boolean { return can(role, 'reports.read_costs'); }

/** Profit and purchase-cost figures are stripped for roles without cost access
 *  -- a cashier seeing unit economics is a data-leak, not a convenience. */
export function redactReportForRole<K extends { cost?: unknown; profit?: unknown; grossProfit?: unknown }>(report: K, role: Role): Omit<K, 'cost' | 'profit' | 'grossProfit'> {
  if (canReadCosts(role)) return report;
  const { cost: _cost, profit: _profit, grossProfit: _grossProfit, ...visible } = report;
  return visible as Omit<K, 'cost' | 'profit' | 'grossProfit'>;
}

export function redactReportCosts(rows: readonly SalesReportRow[], canReadCostData: boolean): SalesReportRow[] {
  return rows.map((row) => (canReadCostData ? { ...row } : redactReportForRole(row, 'cashier')));
}

export function lowStockWarnings(levels: readonly { productId: string; name?: string; available: number }[], reorderLevels: Readonly<Record<string, number>>): LowStockWarning[] {
  return levels
    .filter((level) => reorderLevels[level.productId] !== undefined && level.available < (reorderLevels[level.productId] ?? 0))
    .map((level) => ({ ...level, reorderLevel: reorderLevels[level.productId] ?? 0, shortfall: (reorderLevels[level.productId] ?? 0) - level.available }))
    .sort((a, b) => b.shortfall - a.shortfall || a.productId.localeCompare(b.productId));
}

export function expiryWarnings(batches: readonly { batchId: string; productId: string; lotNumber: string; expiryDate: string; onHand: number }[], asOfDate: string, withinDays: number): ExpiryWarning[] {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(asOfDate)) throw new Error(`Invalid date: ${asOfDate}`);
  if (!Number.isInteger(withinDays) || withinDays < 0) throw new Error('withinDays must be a non-negative integer');
  const asOfMs = Date.parse(`${asOfDate}T00:00:00Z`);
  return batches
    .map((batch) => ({ ...batch, daysUntilExpiry: Math.round((Date.parse(`${batch.expiryDate}T00:00:00Z`) - asOfMs) / MS_PER_DAY) }))
    .filter((batch) => batch.daysUntilExpiry >= 0 && batch.daysUntilExpiry <= withinDays)
    .sort((a, b) => a.expiryDate.localeCompare(b.expiryDate) || a.batchId.localeCompare(b.batchId))
    .map(({ batchId, productId, lotNumber, expiryDate, daysUntilExpiry, onHand }) => ({ batchId, productId, lotNumber, expiryDate, daysUntilExpiry, onHand }));
}

/** Chart-friendly numeric series; money values lose sub-taka precision only for
 *  plotting -- table output keeps exact strings via `kpisToTableRows`. */
export function seriesFromMoney(points: readonly { label: string; amount: MoneyValue }[]): ChartPoint[] {
  return points.map(({ label, amount }) => ({ label, value: Number(amount.amount) }));
}

export function countSeries(points: readonly { label: string; value: number }[]): ChartPoint[] {
  return points.map(({ label, value }) => ({ label, value }));
}

export function kpisToTableRows(kpis: SalesKpis): TableRow[] {
  const rows: TableRow[] = [
    { label: 'Gross', value: kpis.gross.amount },
    { label: 'Refunds', value: kpis.refunds.amount },
    { label: 'Net', value: kpis.net.amount },
    { label: 'Due', value: kpis.due.amount },
  ];
  if (kpis.profit) rows.push({ label: 'Profit', value: kpis.profit.amount });
  return rows;
}

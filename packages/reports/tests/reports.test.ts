import { describe, expect, it } from 'vitest';
import { money, type MoneyValue } from '@pharmacy/money';
import {
  buildDashboardKpis,
  combinePageKpis,
  dayRangeBounds,
  expenseTotal,
  expiryWarnings,
  kpisToTableRows,
  lowStockWarnings,
  paymentBreakdown,
  profitKpi,
  redactReportCosts,
  redactReportForRole,
  rowsInRange,
  summarizePage,
  summarizeSales,
  todaySalesKpi,
  validateReportFilters,
  type SalesReportRow,
  type ProfitKpi,
} from '../src/index';

const m = (amount: string): MoneyValue => money(amount);
const row = (overrides: Partial<SalesReportRow> & Pick<SalesReportRow, 'storeId' | 'gross'>): SalesReportRow => ({ refunds: m('0.00'), due: m('0.00'), ...overrides });

const sampleRow: SalesReportRow = { storeId: 'st1', gross: m('100.55'), refunds: m('10.05'), due: m('5.25'), cost: m('60.50') };

describe('filter validation and day boundaries', () => {
  it('validates explicit instants and range order', () => {
    expect(validateReportFilters({ organizationId: 'o1', storeIds: ['st1'], from: '2026-08-21T00:00:00Z', to: '2026-08-22T00:00:00Z', timezone: 'Asia/Dhaka' })).toEqual({ organizationId: 'o1', storeIds: ['st1'], from: '2026-08-21T00:00:00Z', to: '2026-08-22T00:00:00Z', timezone: 'Asia/Dhaka' });
    expect(() => validateReportFilters({ organizationId: 'o1', from: '2026-08-22T00:00:00Z', to: '2026-08-21T00:00:00Z', timezone: 'UTC' })).toThrow('non-empty');
    expect(() => validateReportFilters({ organizationId: 'o1', from: 'yesterday', to: '2026-08-22T00:00:00Z', timezone: 'UTC' })).toThrow('Invalid from');
    expect(() => validateReportFilters({ organizationId: 'o1', from: '2026-08-21T00:00:00Z', to: '2026-08-22T00:00:00Z', timezone: 'UTC', limit: 1001 })).toThrow('limit');
  });

  it('computes local day bounds with explicit UTC offsets', () => {
    const dhaka = dayRangeBounds('2026-08-21', 360);
    expect(dhaka).toEqual({ from: '2026-08-20T18:00:00.000Z', to: '2026-08-21T18:00:00.000Z' });
    const utc = dayRangeBounds('2026-08-21', 0);
    expect(utc).toEqual({ from: '2026-08-21T00:00:00.000Z', to: '2026-08-22T00:00:00.000Z' });
    const negative = dayRangeBounds('2026-01-01', -300);
    expect(negative.from).toBe('2026-01-01T05:00:00.000Z');
  });

  it('keeps a sale at the local boundary in exactly one day', () => {
    const dhaka = dayRangeBounds('2026-08-21', 360);
    const saleAtMidnightLocal = { createdAt: '2026-08-20T18:00:00.000Z', amount: 1 };
    const saleAtEndOfDay = { createdAt: '2026-08-21T17:59:59.999Z', amount: 2 };
    const saleNextDay = { createdAt: '2026-08-21T18:00:00.000Z', amount: 4 };
    expect(rowsInRange([saleNextDay, saleAtEndOfDay, saleAtMidnightLocal], dhaka)).toEqual([saleAtMidnightLocal, saleAtEndOfDay]);
  });
});

describe('KPI aggregation with money math', () => {
  it('summarizes gross, refunds, net, due, and profit in exact cents', () => {
    expect(summarizeSales([sampleRow], false)).toEqual({ gross: m('100.55'), refunds: m('10.05'), net: m('90.50'), due: m('5.25') });
    expect(summarizeSales([sampleRow], true).profit).toEqual(m('30.00'));
  });

  it('handles refunds pushing net down and dues across many rows', () => {
    const rows = [row({ storeId: 'st1', gross: m('0.10'), refunds: m('0.10') }), row({ storeId: 'st1', gross: m('0.02'), due: m('0.03') })];
    expect(summarizeSales(rows, false)).toMatchObject({ net: m('0.02'), due: m('0.03') });
  });

  it('breaks payments down by method and totals expenses', () => {
    expect(paymentBreakdown([
      { method: 'cash', amount: m('100.00') },
      { method: 'bkash', amount: m('50.25') },
      { method: 'cash', amount: m('0.75') },
      { method: 'due', amount: m('9.99') },
    ])).toEqual({ cash: m('100.75'), bkash: m('50.25'), due: m('9.99') });
    expect(expenseTotal([{ id: 'e1', category: 'rent', amount: m('1000.00'), spentAt: '2026-08-21T00:00:00Z' }, { id: 'e2', category: 'power', amount: m('250.10'), spentAt: '2026-08-21T01:00:00Z' }])).toEqual(m('1250.10'));
  });

  it('builds today KPIs and profit separately', () => {
    expect(todaySalesKpi([sampleRow], 7)).toEqual({ gross: m('100.55'), refunds: m('10.05'), net: m('90.50'), transactionCount: 7 });
    expect(profitKpi([sampleRow])).toEqual({ revenue: m('90.50'), cost: m('60.50'), grossProfit: m('30.00') });
  });

  it('builds a role-scoped dashboard', () => {
    const input = { salesRows: [sampleRow], payments: [{ method: 'cash' as const, amount: m('95.50') }], expenses: [{ id: 'e1', category: 'rent', amount: m('500.00'), spentAt: '2026-08-21T00:00:00Z' }], transactionCount: 3 };
    const ownerView = buildDashboardKpis(input, 'owner');
    expect(ownerView.profit).toBeDefined();
    expect(ownerView.expenses).toEqual(m('500.00'));
    const cashierView = buildDashboardKpis(input, 'cashier');
    expect(cashierView.profit).toBeUndefined();
    expect(cashierView.sales.net).toEqual(m('90.50'));
  });
});

describe('role redaction', () => {
  it('strips profit for cashiers but keeps it for owner/manager', () => {
    expect(redactReportForRole(sampleRow, 'cashier')).not.toHaveProperty('cost');
    expect(redactReportForRole({ gross: m('1.00'), profit: m('0.50') }, 'cashier')).toEqual({ gross: m('1.00') });
    expect(redactReportForRole(sampleRow, 'owner')).toEqual(sampleRow);
    expect(redactReportForRole(profitKpi([sampleRow]), 'inventory_staff')).toEqual({ revenue: m('90.50') });
    const managerProfit = redactReportForRole(profitKpi([sampleRow]), 'manager');
    expect((managerProfit as ProfitKpi).grossProfit).toEqual(m('30.00'));  });

  it('redacts row-level costs by flag', () => {
    const [visible] = redactReportCosts([sampleRow], false);
    expect(visible).not.toHaveProperty('cost');
    expect(redactReportCosts([sampleRow], true)[0]).toEqual(sampleRow);
  });
});

describe('pagination-safe aggregation', () => {
  it('combines per-page KPIs exactly across page seams', () => {
    const pages = [
      { items: [row({ storeId: 'st1', gross: m('33.33'), due: m('1.11'), cost: m('10.00') }), row({ storeId: 'st1', gross: m('0.67') })] },
      { items: [row({ storeId: 'st1', gross: m('100.00'), cost: m('40.01') })] },
    ];
    const perPage = pages.map((page) => summarizePage(page, true));
    expect(combinePageKpis(perPage, true)).toEqual(summarizeSales(pages.flatMap((page) => page.items), true));
    expect(combinePageKpis(perPage, false).profit).toBeUndefined();
  });

  it('keeps cents intact over many small pages', () => {
    const pageItems = Array.from({ length: 250 }, (_, index) => row({ storeId: 'st1', gross: m('0.01'), due: m('0.01') }));
    const chunked = [pageItems.slice(0, 100), pageItems.slice(100, 200), pageItems.slice(200)];
    const kpis = combinePageKpis(chunked.map((items) => summarizePage({ items }, false)), false);
    expect(kpis.gross).toEqual(m('2.50'));
    expect(kpis.due).toEqual(m('2.50'));
  });
});

describe('stock and expiry warnings', () => {
  it('flags low stock sorted by shortfall', () => {
    const warnings = lowStockWarnings([
      { productId: 'p1', name: 'Napa', available: 8 },
      { productId: 'p2', name: 'Seclo', available: 1 },
      { productId: 'p3', available: 50 },
    ], { p1: 10, p2: 20, p3: 5 });
    expect(warnings.map((warning) => warning.productId)).toEqual(['p2', 'p1']);
    expect(warnings[0]).toMatchObject({ shortfall: 19, reorderLevel: 20 });
  });

  it('warns only within the expiry window with day counts', () => {
    const batches = [
      { batchId: 'b1', productId: 'p1', lotNumber: 'L1', expiryDate: '2026-08-28', onHand: 12 },
      { batchId: 'b2', productId: 'p2', lotNumber: 'L2', expiryDate: '2026-09-20', onHand: 3 },
      { batchId: 'b3', productId: 'p3', lotNumber: 'L3', expiryDate: '2026-08-27', onHand: 1 },
      { batchId: 'b4', productId: 'p4', lotNumber: 'L4', expiryDate: '2026-08-26', onHand: 5 },
    ];
    const warnings = expiryWarnings(batches, '2026-08-21', 7);
    expect(warnings.map((warning) => warning.batchId)).toEqual(['b4', 'b3', 'b1']);
    expect(warnings[0]).toMatchObject({ daysUntilExpiry: 5, onHand: 5 });
    expect(expiryWarnings(batches, '2026-08-21', 0)).toHaveLength(0);
    expect(() => expiryWarnings(batches, 'nope', 7)).toThrow();
    expect(() => expiryWarnings(batches, '2026-08-21', -1)).toThrow();
  });
});

describe('formatting helpers', () => {
  it('produces chart series and table rows', () => {
    expect(kpisToTableRows(summarizeSales([sampleRow], true))).toEqual([
      { label: 'Gross', value: '100.55' },
      { label: 'Refunds', value: '10.05' },
      { label: 'Net', value: '90.50' },
      { label: 'Due', value: '5.25' },
      { label: 'Profit', value: '30.00' },
    ]);
    expect(kpisToTableRows(summarizeSales([sampleRow], false))).toHaveLength(4);
  });
});

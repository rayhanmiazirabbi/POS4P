import { describe, expect, it } from 'vitest';
import { redactReportCosts, summarizeSales, validateReportFilters, type SalesReportRow } from '../src/index';

const rows: SalesReportRow[] = [{ storeId: 'st1', gross: '100.00', refunds: '10.00', due: '5.00', cost: '60.00' }];
describe('reports', () => {
  it('validates explicit time boundaries and redacts costs', () => { expect(validateReportFilters({ organizationId: 'o1', from: '2026-08-21T00:00:00Z', to: '2026-08-22T00:00:00Z', timezone: 'Asia/Dhaka' }).timezone).toBe('Asia/Dhaka'); expect(redactReportCosts(rows, false)[0]).not.toHaveProperty('cost'); });
  it('summarizes net sales and role-allowed profit', () => { expect(summarizeSales(rows, false)).toEqual({ gross: '100.00', refunds: '10.00', net: '90.00', due: '5.00' }); expect(summarizeSales(rows, true).profit).toBe('30.00'); });
});

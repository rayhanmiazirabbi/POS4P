import { expect, it } from 'vitest';
import { can } from '../src/index';

it('defaults to least privilege', () => {
  expect(can('cashier', 'sales.create')).toBe(true);
  expect(can('cashier', 'reports.read_costs')).toBe(false);
  expect(can('inventory_staff', 'sales.refund')).toBe(false);
});

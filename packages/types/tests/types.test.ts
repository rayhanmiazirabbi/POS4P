import { expect, it } from 'vitest';
import type { Currency, Role } from '../src/index';

it('exposes explicit money and role contracts', () => {
  const currency: Currency = 'BDT';
  const role: Role = 'cashier';
  expect({ currency, role }).toEqual({ currency: 'BDT', role: 'cashier' });
});

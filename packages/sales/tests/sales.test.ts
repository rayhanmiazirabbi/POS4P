import { describe, expect, it } from 'vitest';
import { allocateLineDiscounts, calculateSaleTotals, createSaleSnapshot, validateReturn, type SaleLine } from '../src/index';
import { add, money } from '@pharmacy/money';

const line: SaleLine = { id: 'l1', productId: 'p1', name: 'Item', quantity: 2, unitPrice: money('10.00'), discount: money('1.00'), tax: money('0.50') };

describe('sales', () => {
  it('calculates immutable totals and payment due', () => {
    const sale = createSaleSnapshot({ id: 's1', customerId: null, lines: [line], createdAt: '2026-08-21T00:00:00Z', payments: [{ method: 'cash', amount: money('15.00') }] });
    expect(sale.totals).toMatchObject({ subtotal: money('20.00'), discount: money('1.00'), tax: money('0.50'), total: money('19.50'), due: money('4.50') });
    expect(Object.isFrozen(sale)).toBe(true);
    expect(() => { (sale.totals.total as { amount: string }).amount = '0.00'; }).toThrow();
  });
  it('never permits returns above remaining quantity', () => {
    const sale = createSaleSnapshot({ id: 's1', customerId: null, lines: [line], createdAt: '2026-08-21T00:00:00Z' });
    expect(() => validateReturn({ saleId: 's1', lines: [{ saleLineId: 'l1', quantity: 2 }] }, sale, { l1: 1 })).toThrow('exceeds');
    expect(() => validateReturn({ saleId: 's1', lines: [{ saleLineId: 'l1', quantity: 1 }] }, sale)).not.toThrow();
  });
  it('keeps calculation pure', () => { expect(calculateSaleTotals([line]).total.amount).toBe('19.50'); });
  it('allocates an order-level discount across lines without losing a cent', () => {
    const lines: SaleLine[] = [
      { ...line, id: 'a', unitPrice: money('33.34'), discount: money('0.00'), quantity: 1 },
      { ...line, id: 'b', unitPrice: money('33.33'), discount: money('0.00'), quantity: 1 },
      { ...line, id: 'c', unitPrice: money('33.33'), discount: money('0.00'), quantity: 1 },
    ];
    const parts = allocateLineDiscounts(lines, money('10.00'));
    expect(parts.map((part) => part.amount)).toEqual(['3.34', '3.33', '3.33']);
    expect(add(...parts).amount).toBe('10.00');
  });
});

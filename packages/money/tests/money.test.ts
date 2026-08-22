import { describe, expect, it } from 'vitest';
import table from '../fixtures/parity.json';
import {
  add, allocate, change, compare, due, formatMoney, isNegative, isZero,
  money, multiply, round, subtract, type MoneyValue, type RoundingMode,
} from '../src/index';

describe('money', () => {
  it('calculates with decimal-safe integer cents', () => {
    expect(multiply(money('10.25'), 3)).toEqual({ amount: '30.75', currency: 'BDT' });
    expect(add(money('0.10'), money('0.20'))).toEqual({ amount: '0.30', currency: 'BDT' });
  });
  it('calculates due and change', () => {
    expect(due(money('100.00'), money('40.00')).amount).toBe('60.00');
    expect(change(money('100.00'), money('140.00')).amount).toBe('40.00');
  });
  it('rejects mixed currencies and invalid amounts', () => {
    expect(() => money('1.001')).toThrow('Invalid decimal');
    expect(() => add(money('1.00'), { amount: '1.00', currency: 'USD' as never })).toThrow('Currency mismatch');
    expect(() => subtract(money('1.00'), { amount: '1.00', currency: 'USD' as never })).toThrow('Currency mismatch');
  });

  describe('subtract, compare, and predicates', () => {
    it('subtracts without string interpolation', () => {
      expect(subtract(money('100.00'), money('40.00')).amount).toBe('60.00');
      expect(subtract(money('40.00'), money('100.00')).amount).toBe('-60.00');
      expect(subtract(money('10.00'), money('10.00')).amount).toBe('0.00');
    });
    it('compares and classifies', () => {
      expect(compare(money('5.00'), money('5.00'))).toBe(0);
      expect(compare(money('4.99'), money('5.00'))).toBe(-1);
      expect(compare(money('5.01'), money('5.00'))).toBe(1);
      expect(compare(money('5.00'), money('5.00'))).toBe(0);
      expect(isZero(money('0.00'))).toBe(true);
      expect(isZero(money('-0.00'))).toBe(true);
      expect(isZero(money('0.01'))).toBe(false);
      expect(isNegative(money('-0.01'))).toBe(true);
      expect(isNegative(money('0.01'))).toBe(false);
      expect(isNegative(money('0.00'))).toBe(false);
    });
  });

  describe('rounding', () => {
    it('rounds half-up for cash tender', () => {
      expect(round('10.255', 'half-up').amount).toBe('10.26');
      expect(round('10.245', 'half-up').amount).toBe('10.25');
      expect(round('0.005', 'half-up').amount).toBe('0.01');
    });
    it('rounds half-even to avoid accumulated bias', () => {
      expect(round('10.255', 'half-even').amount).toBe('10.26');
      expect(round('10.245', 'half-even').amount).toBe('10.24');
      expect(round('0.005', 'half-even').amount).toBe('0.00');
      expect(round('1.005', 'half-even').amount).toBe('1.00');
    });
    it('supports truncation and away-from-zero', () => {
      expect(round('10.249', 'down').amount).toBe('10.24');
      expect(round('10.241', 'up').amount).toBe('10.25');
      expect(round('-10.241', 'up').amount).toBe('-10.25');
    });
    it('rejects malformed decimals', () => {
      expect(() => round('1.2.3', 'half-up')).toThrow('Invalid decimal');
      expect(() => round('abc', 'half-up')).toThrow('Invalid decimal');
    });
  });

  describe('allocation', () => {
    it('splits three ways with no lost cent', () => {
      expect(allocate(money('100.00'), [1, 1, 1]).map((part) => part.amount)).toEqual(['33.34', '33.33', '33.33']);
    });
    it('always sums exactly to the total', () => {
      const parts = allocate(money('13.37'), [2, 1]);
      expect(parts.map((part) => part.amount)).toEqual(['8.92', '4.45']);
      expect(add(...parts).amount).toBe('13.37');
    });
    it('handles split tenders and zero weights', () => {
      expect(allocate(money('100.00'), [0.5, 0.5]).map((part) => part.amount)).toEqual(['50.00', '50.00']);
      expect(allocate(money('100.00'), [1, 0, 1]).map((part) => part.amount)).toEqual(['50.00', '0.00', '50.00']);
      expect(allocate(money('0.01'), [1, 1, 1]).map((part) => part.amount)).toEqual(['0.01', '0.00', '0.00']);
    });
    it('rejects negative totals and empty or all-zero weights', () => {
      expect(() => allocate(money('-1.00'), [1, 1])).toThrow('negative');
      expect(() => allocate(money('1.00'), [])).toThrow('At least one weight');
      expect(() => allocate(money('1.00'), [0, 0])).toThrow('not all be zero');
      expect(() => allocate(money('1.00'), [-1, 2])).toThrow('non-negative');
    });
  });

  describe('formatting and serialization', () => {
    it('formats with the taka sign and grouping', () => {
      expect(formatMoney(money('1234.56'))).toBe('৳1,234.56');
      expect(formatMoney(money('0.05'))).toBe('৳0.05');
      expect(formatMoney(money('-1000000.00'))).toBe('-৳1,000,000.00');
    });
    it('round-trips through money and format', () => {
      for (const amount of ['0.00', '12.34', '999999.99', '-0.01']) {
        const value: MoneyValue = money(amount);
        expect(money(value.amount)).toEqual(value);
        expect(round(value.amount, 'half-even')).toEqual(value);
      }
    });
  });

  // The same table drives backend/tests/test_money_parity.py, so both
  // implementations are pinned to identical inputs and outputs.
  describe('parity with backend Decimal behaviour', () => {
    it('matches the shared fixture table', () => {
      for (const row of table.add) expect(add(money(row.a), money(row.b)).amount, `${row.a}+${row.b}`).toBe(row.expected);
      for (const row of table.subtract) expect(subtract(money(row.a), money(row.b)).amount, `${row.a}-${row.b}`).toBe(row.expected);
      for (const row of table.multiply) expect(multiply(money(row.a), row.n).amount, `${row.a}*${row.n}`).toBe(row.expected);
      for (const row of table.round) expect(round(row.value, row.mode as RoundingMode).amount, `${row.value}/${row.mode}`).toBe(row.expected);
      for (const row of table.allocate) {
        expect(allocate(money(row.total), row.weights).map((part) => part.amount), row.total).toEqual(row.expected);
      }
    });
  });
});

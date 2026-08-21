import { describe, expect, it } from 'vitest';
import { add, change, due, money, multiply } from '../src/index';

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
  });
});

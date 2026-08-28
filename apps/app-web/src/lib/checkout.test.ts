import { describe, expect, it } from 'vitest';

import { amountDueNow, calculateCheckout } from './checkout';

describe('calculateCheckout', () => {
  it('applies line then global discounts before separate charges', () => {
    const total = calculateCheckout(
      [
        { id: 'a', quantity: 2, unitPrice: '50.00', discount: { mode: 'percentage', value: '10' } },
        { id: 'b', quantity: 1, unitPrice: '20.00', discount: { mode: 'flat', value: '5.00' } },
      ],
      { mode: 'percentage', value: '10' },
      [
        { kind: 'delivery', amount: '15.00' },
        { kind: 'other', amount: '2.00', label: 'Bag' },
      ],
    );
    expect(total).toMatchObject({ subtotal: '120.00', lineDiscount: '15.00', globalDiscount: '10.50', total: '111.50' });
  });

  it('rounds percentage discounts half-up per line', () => {
    expect(calculateCheckout([{ id: 'a', quantity: 1, unitPrice: '0.05', discount: { mode: 'percentage', value: '10' } }]).lineDiscount).toBe('0.01');
  });

  it('validates caps, labels, and advance', () => {
    expect(() => calculateCheckout([{ id: 'a', quantity: 1, unitPrice: '5.00', discount: { mode: 'flat', value: '6.00' } }])).toThrow(/cannot exceed/);
    expect(() => calculateCheckout([{ id: 'a', quantity: 1, unitPrice: '5.00' }], undefined, [{ kind: 'other', amount: '1.00' }])).toThrow(/label/);
    expect(() => amountDueNow('5.00', '6.00')).toThrow(/Advance/);
  });
});

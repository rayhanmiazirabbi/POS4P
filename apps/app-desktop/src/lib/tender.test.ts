import { describe, expect, it } from 'vitest';

import { splitTender } from '@pharmacy/sales';

import { buildPayments, defaultDigitalMethod, digitalLabel, digitalMethods } from './tender';

describe('desktop tender methods', () => {
  it('offers bKash and Nagad beside cash, defaulting to bKash', () => {
    expect([...digitalMethods]).toEqual(['bkash', 'nagad']);
    expect(digitalMethods).not.toContain('cash');
    expect(defaultDigitalMethod).toBe('bkash');
    expect(digitalLabel('bkash')).toBe('bKash');
    expect(digitalLabel('nagad')).toBe('Nagad');
  });

  it('records a pure-cash sale as cash no matter which wallet is selected', () => {
    // Regression: the till used to hardcode `tenderPayments(split, 'bkash')`, so
    // the method rode along with every post regardless of what was taken.
    const split = splitTender('100.00', '', '');
    for (const method of digitalMethods) {
      expect(buildPayments(split, method).map((payment) => payment.method)).toEqual(['cash']);
    }
  });

  it('names the chosen wallet only for the digital portion of the split', () => {
    const split = splitTender('100.00', '40', '60');

    expect(buildPayments(split, 'nagad').map((payment) => [payment.method, payment.amount.amount])).toEqual([
      ['cash', '40.00'],
      ['nagad', '60.00'],
    ]);
    expect(buildPayments(split, 'bkash').at(-1)?.method).toBe('bkash');
  });

  it('leaves an unused wallet out of the rows entirely', () => {
    // A zero bkash line would be a payment that never happened sitting in the
    // day's mix -- `tenderPayments` omits it, and this mapping must not undo that.
    const split = splitTender('100.00', '', '');

    expect(buildPayments(split, 'bkash').map((payment) => payment.method)).toEqual(['cash']);
  });

  it('carries the received amount on cash so the drawer reconciles', () => {
    const split = splitTender('100.00', '120', '');

    expect(buildPayments(split, defaultDigitalMethod)[0]).toMatchObject({
      method: 'cash',
      amount: { amount: '100.00' },
      receivedAmount: { amount: '120.00' },
    });
  });
});

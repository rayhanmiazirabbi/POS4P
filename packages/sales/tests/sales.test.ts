import { describe, expect, it } from 'vitest';
import {
  allocateLineDiscounts,
  calculateSaleTotals,
  cashChange,
  createSaleSnapshot,
  formatReceiptText,
  provisionalReceipt,
  readTender,
  receiptFromSale,
  splitTender,
  tenderPayments,
  validateReturn,
  validateSalePayments,
  wirePayments,
  type Payment,
  type SaleLine,
} from '../src/index';
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

/**
 * The counter's payment split.
 *
 * This logic used to live inline in the web POS page as float cents
 * (`Math.round(Number(value) * 100)`), which is why it had no tests: there was
 * nothing importable to test. The float arithmetic was the smaller problem -- the
 * parser returned `0` for anything it could not read, so a mistyped tender
 * silently moved the money to another line and the sale posted anyway.
 */
describe('readTender', () => {
  it('normalises to the money scale so "40" posts as "40.00"', () => {
    expect(readTender('40')?.amount).toBe('40.00');
    expect(readTender('40.5')?.amount).toBe('40.50');
    expect(readTender('  250.50 ')?.amount).toBe('250.50');
  });

  it('refuses what it cannot read rather than calling it zero', () => {
    // Zero is the dangerous answer, not an error. Reading a mistyped tender as
    // 0.00 posts the sale for money the till never took and quietly pushes the
    // balance onto cash or onto the customer's due account.
    for (const bad of ['', ' ', 'abc', '1,200', '12.', '1e3', '250.505', 'NaN']) {
      expect(readTender(bad), bad).toBeNull();
    }
  });

  it('refuses a negative tender', () => {
    // A leading minus in a tender box is a typo, not a refund. Honouring it would
    // post a sale that pays out at the till.
    expect(readTender('-5.00')).toBeNull();
  });
});

describe('splitTender', () => {
  it('charges the exact total when the cash box is left blank', () => {
    expect(splitTender('123.45', '', '')).toEqual({
      cash: '123.45', digital: '0.00', due: '0.00', change: '0.00', readable: true,
    });
  });

  it('returns change on an overpayment and never a negative due', () => {
    expect(splitTender('123.45', '500', '')).toEqual({
      cash: '123.45', digital: '0.00', due: '0.00', change: '376.55', readable: true,
    });
  });

  it('sends short cash to the due line, not to a negative cash line', () => {
    expect(splitTender('100.00', '40', '')).toEqual({
      cash: '40.00', digital: '0.00', due: '60.00', change: '0.00', readable: true,
    });
  });

  it('clamps a digital amount above the total', () => {
    // The two fields are edited independently. Unclamped, a digital amount over
    // the total drove the cash and due lines negative and the sale posted a split
    // the till never took.
    expect(splitTender('100.00', '0', '500')).toEqual({
      cash: '0.00', digital: '100.00', due: '0.00', change: '0.00', readable: true,
    });
  });

  it('splits exactly, with the parts summing to the total', () => {
    expect(splitTender('100.00', '40', '60')).toEqual({
      cash: '40.00', digital: '60.00', due: '0.00', change: '0.00', readable: true,
    });
  });

  it('reports no change when a digital tender covers the total and the cash box is untouched', () => {
    // A blank cash box means "the exact amount still owed", which on a
    // digital-only sale is nothing. Imputing the whole total instead put the
    // entire sale value on the change line, so the screen told the cashier to
    // hand back money that had never entered the drawer.
    expect(splitTender('100.00', '', '100.00')).toEqual({
      cash: '0.00', digital: '100.00', due: '0.00', change: '0.00', readable: true,
    });
    // Same when the digital amount overshoots and is clamped back to the total.
    expect(splitTender('100.00', '', '150.00')).toEqual({
      cash: '0.00', digital: '100.00', due: '0.00', change: '0.00', readable: true,
    });
  });

  it('lets an untouched cash box settle the balance a partial digital tender left', () => {
    expect(splitTender('100.00', '', '40.00')).toEqual({
      cash: '60.00', digital: '40.00', due: '0.00', change: '0.00', readable: true,
    });
  });

  it('still returns change on cash typed over a total the digital tender then covered', () => {
    // The cashier took a note and the customer then paid digitally: the note goes
    // straight back, so no cash row is written but the change is real.
    expect(splitTender('100.00', '50.00', '100.00')).toEqual({
      cash: '0.00', digital: '100.00', due: '0.00', change: '50.00', readable: true,
    });
  });

  it('blocks the sale when either field is unreadable', () => {
    for (const bad of ['abc', '1,200', '-5', '1e3', '12.']) {
      expect(splitTender('100.00', bad, '').readable, `cash ${bad}`).toBe(false);
      expect(splitTender('100.00', '', bad).readable, `digital ${bad}`).toBe(false);
    }
  });

  it('reports zeroes rather than throwing while a field is unreadable', () => {
    // The split is recomputed on every keystroke during render, so a half-typed
    // amount must not throw -- a `money` exception here blanks the POS screen and
    // takes the cart with it.
    expect(splitTender('100.00', '12.', '')).toEqual({
      cash: '0.00', digital: '0.00', due: '100.00', change: '0.00', readable: false,
    });
  });

  it('treats a blank digital box as unused, not as an unreadable field', () => {
    // The two blanks mean different things: no cash typed means "exact total",
    // no digital typed means "that tender was not used".
    expect(splitTender('100.00', '', '').readable).toBe(true);
  });
});

describe('tenderPayments', () => {
  it('posts the cash tender with what was handed over, not what the sale took', () => {
    const payments = tenderPayments(splitTender('100.00', '500', ''), 'bkash');
    expect(payments).toEqual([{ method: 'cash', amount: money('100.00'), receivedAmount: money('500.00') }]);
  });

  it('leaves out a tender that was not used', () => {
    // A zero bkash row is a payment that never happened sitting in the day's
    // payment mix, and one more `payments` row for the shop to explain.
    expect(tenderPayments(splitTender('100.00', '', ''), 'bkash').map((payment) => payment.method)).toEqual(['cash']);
    expect(tenderPayments(splitTender('100.00', '40', '60'), 'nagad').map((payment) => payment.method)).toEqual(['cash', 'nagad']);
    expect(tenderPayments(splitTender('100.00', '40', ''), 'bkash').map((payment) => payment.method)).toEqual(['cash', 'due']);
  });

  it('still sends one tender for a cart that totals nothing', () => {
    // The API requires at least one payment, and a 422 about an empty list is not
    // something a cashier can act on. A zero cash tender is the truth here.
    expect(tenderPayments(splitTender('0.00', '', ''), 'bkash')).toEqual([
      { method: 'cash', amount: money('0.00'), receivedAmount: money('0.00') },
    ]);
  });

  it('never carries a received amount on a wallet tender', () => {
    // `create_sale_payment` refuses any non-cash tender whose received amount
    // differs from its amount -- change is a cash concept.
    for (const payment of tenderPayments(splitTender('100.00', '0', '100'), 'bkash')) {
      if (payment.method !== 'cash') expect(payment.receivedAmount).toBeUndefined();
    }
  });

  it('produces tenders the server accepts, for every split it can produce', () => {
    for (const [cash, digital] of [['', ''], ['500', ''], ['40', '60'], ['0', '100'], ['100', '0']]) {
      const total = money('100.00');
      const payments = tenderPayments(splitTender(total.amount, cash ?? '', digital ?? ''), 'bkash');
      expect(() => validateSalePayments(payments, total, { hasCustomer: true }), `${cash}/${digital}`).not.toThrow();
    }
  });
});

describe('validateSalePayments', () => {
  const total = money('100.00');

  it('requires the tenders to add up to the total exactly', () => {
    // `create_sale` rolls the whole transaction back with "Payments must add up to
    // the sale total". Catching it here saves a cart the cashier has already
    // cleared and a customer already walking away.
    expect(() => validateSalePayments([{ method: 'cash', amount: money('99.00'), receivedAmount: money('99.00') }], total, { hasCustomer: false }))
      .toThrow('not the sale total');
  });

  it('requires a customer before booking a due balance', () => {
    const payments: Payment[] = [{ method: 'due', amount: total }];
    expect(() => validateSalePayments(payments, total, { hasCustomer: false })).toThrow('require a customer');
    expect(() => validateSalePayments(payments, total, { hasCustomer: true })).not.toThrow();
  });

  it('requires a received amount on cash and refuses short cash', () => {
    expect(() => validateSalePayments([{ method: 'cash', amount: total }], total, { hasCustomer: false }))
      .toThrow('require a received amount');
    expect(() => validateSalePayments([{ method: 'cash', amount: total, receivedAmount: money('40.00') }], total, { hasCustomer: false }))
      .toThrow('short by ৳60.00');
  });

  it('refuses change on a wallet tender', () => {
    expect(() => validateSalePayments([{ method: 'bkash', amount: total, receivedAmount: money('500.00') }], total, { hasCustomer: false }))
      .toThrow('Only cash payments may carry change');
  });

  it('refuses a negative tender', () => {
    expect(() => validateSalePayments(
      [{ method: 'cash', amount: money('-1.00'), receivedAmount: money('-1.00') }, { method: 'bkash', amount: money('101.00') }],
      total,
      { hasCustomer: false },
    )).toThrow('cannot be negative');
  });

  it('refuses a sale with no tender at all', () => {
    expect(() => validateSalePayments([], total, { hasCustomer: false })).toThrow('at least one payment');
  });
});

describe('wirePayments', () => {
  it('omits the optional keys rather than sending them as null', () => {
    // `ApiModel` sets `extra="forbid"`, and `receivedAmount: undefined` serialises
    // to a key JSON.stringify drops -- but only if it is absent, not null. Doing
    // this per shell is how one of them sent a null the API refused.
    expect(wirePayments([{ method: 'bkash', amount: money('60.00') }])).toEqual([{ method: 'bkash', amount: '60.00' }]);
    expect(wirePayments([{ method: 'cash', amount: money('40.00'), receivedAmount: money('50.00') }]))
      .toEqual([{ method: 'cash', amount: '40.00', receivedAmount: '50.00' }]);
  });
});

describe('receipt', () => {
  const header = { organizationName: 'Rahman Pharmacy', storeName: 'Dhanmondi' };
  const lines: SaleLine[] = [
    { id: 'l1', productId: 'p1', name: 'Paracetamol 500mg', quantity: 2, unitPrice: money('12.50'), discount: money('0.00'), tax: money('0.00') },
  ];
  const payments: Payment[] = [{ method: 'cash', amount: money('25.00'), receivedAmount: money('50.00') }];
  const filed = {
    id: 's1', receiptNumber: 'R-00000042', createdAt: '2026-08-21T09:15:00Z',
    subtotal: '25.00', discount: '0.00', total: '25.00',
    items: [{ productName: 'Paracetamol 500mg', quantity: '2', unitPrice: '12.50', lineTotal: '25.00' }],
    payments: [{ method: 'cash' as const, amount: '25.00', receivedAmount: '50.00' }],
  };

  it('reports change from the cash tenders', () => {
    expect(cashChange(payments).amount).toBe('25.00');
    // A wallet tender never contributes change, even if a caller sets the field.
    expect(cashChange([{ method: 'bkash', amount: money('25.00') }]).amount).toBe('0.00');
  });

  it('is printable for a sale that has not reached the server yet', () => {
    // An offline sale has no receipt number: `_next_receipt_number` locks a
    // per-store counter so two tills cannot print the same one, and a number this
    // client invented would later belong to a different sale. The customer still
    // gets a slip, and the slip says what it is.
    const receipt = provisionalReceipt({ ...header, issuedAt: '2026-08-21T09:15:00Z', lines, payments });
    expect(receipt.receiptNumber).toBeNull();
    expect(receipt.saleId).toBeNull();
    expect(receipt.totals.total.amount).toBe('25.00');
    expect(formatReceiptText(receipt)).toContain('RECEIPT PENDING UPLOAD');
  });

  it('shows the number, the tender and the change once filed', () => {
    const receipt = receiptFromSale(filed, { ...header, customerName: 'Ayesha · 01711000000' });
    expect(receipt.totals.total.amount).toBe('25.00');
    expect(receipt.change.amount).toBe('25.00');
    const text = formatReceiptText(receipt);
    expect(text).toContain('Receipt R-00000042');
    expect(text).toContain('Paracetamol 500mg x2  ৳25.00');
    expect(text).toContain('TOTAL ৳25.00');
    expect(text).toContain('CASH ৳25.00');
    expect(text).toContain('CHANGE ৳25.00');
    expect(text).toContain('Ayesha · 01711000000');
  });

  it('prints the server figures, not the cart it was rung up from', () => {
    // The server recomputes from `store_products.sale_price` and ignores the
    // client's echoed totals, so a price changed since the shelf loaded is charged
    // at the new one. A slip printed from the cart would disagree with what came
    // out of the customer's pocket.
    const repriced = receiptFromSale(
      { ...filed, total: '30.00', subtotal: '30.00', items: [{ productName: 'Paracetamol 500mg', quantity: '2', unitPrice: '15.00', lineTotal: '30.00' }], payments: [{ method: 'cash', amount: '30.00', receivedAmount: '50.00' }] },
      header,
    );
    expect(repriced.totals.total.amount).toBe('30.00');
    expect(repriced.change.amount).toBe('20.00');
  });

  it('reads a null received amount as absent, the way the API sends it', () => {
    const receipt = receiptFromSale({ ...filed, payments: [{ method: 'bkash', amount: '25.00', receivedAmount: null }] }, header);
    expect(receipt.payments[0]?.receivedAmount).toBeUndefined();
    expect(receipt.change.amount).toBe('0.00');
  });

  it('omits the lines a sale did not have', () => {
    // A zero discount, zero tax or zero change line on a slip is noise the
    // customer has to read past to find what they paid.
    const text = formatReceiptText(receiptFromSale({ ...filed, payments: [{ method: 'cash', amount: '25.00', receivedAmount: '25.00' }] }, header));
    expect(text).not.toContain('CHANGE');
    expect(text).not.toContain('DISCOUNT');
    expect(text).not.toContain('TAX');
  });

  it('freezes the slip so a later cart edit cannot rewrite it', () => {
    const receipt = provisionalReceipt({ ...header, issuedAt: '2026-08-21T09:15:00Z', lines, payments });
    expect(Object.isFrozen(receipt)).toBe(true);
    expect(() => { (receipt.totals.total as { amount: string }).amount = '0.00'; }).toThrow();
    expect(() => { (receipt.lines[0]?.lineTotal as { amount: string }).amount = '0.00'; }).toThrow();
  });
});

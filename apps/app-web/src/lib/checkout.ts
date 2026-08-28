import type { DiscountInput, SaleChargeInput } from '@pharmacy/api';

export type CheckoutLine = {
  id: string;
  quantity: number;
  unitPrice: string;
  discount?: DiscountInput;
};

export type CheckoutLineTotal = CheckoutLine & {
  gross: string;
  discountAmount: string;
  net: string;
};

export type CheckoutTotals = {
  lines: readonly CheckoutLineTotal[];
  subtotal: string;
  lineDiscount: string;
  afterLineDiscounts: string;
  globalDiscount: string;
  deliveryCharge: string;
  otherFee: string;
  total: string;
};

function cents(raw: string): bigint {
  const value = raw.trim();
  if (!/^\d+(\.\d{1,2})?$/.test(value)) throw new Error('Enter an amount with no more than two decimal places');
  const [whole, fraction = ''] = value.split('.');
  return BigInt(`${whole}${fraction.padEnd(2, '0')}`);
}

function amount(value: bigint): string {
  const sign = value < 0n ? '-' : '';
  const absolute = value < 0n ? -value : value;
  return `${sign}${absolute / 100n}.${(absolute % 100n).toString().padStart(2, '0')}`;
}

function discountFor(base: bigint, discount?: DiscountInput): bigint {
  if (!discount || discount.value.trim() === '') return 0n;
  const value = cents(discount.value);
  if (discount.mode === 'flat') {
    if (value > base) throw new Error('Flat discount cannot exceed its eligible subtotal');
    return value;
  }
  if (value > 10_000n) throw new Error('Percentage discount cannot exceed 100');
  // `value` is hundredths of one percent and 100% is therefore 10,000.
  return (base * value + 5_000n) / 10_000n;
}

export function calculateCheckout(
  inputLines: readonly CheckoutLine[],
  globalDiscount?: DiscountInput,
  charges: readonly SaleChargeInput[] = [],
): CheckoutTotals {
  const lines = inputLines.map((line) => {
    if (!Number.isInteger(line.quantity) || line.quantity <= 0) throw new Error('Quantity must be a positive whole number');
    const gross = cents(line.unitPrice) * BigInt(line.quantity);
    const discountAmount = discountFor(gross, line.discount);
    return { ...line, gross: amount(gross), discountAmount: amount(discountAmount), net: amount(gross - discountAmount) };
  });
  const subtotal = lines.reduce((sum, line) => sum + cents(line.gross), 0n);
  const lineDiscount = lines.reduce((sum, line) => sum + cents(line.discountAmount), 0n);
  const afterLines = subtotal - lineDiscount;
  const global = discountFor(afterLines, globalDiscount);
  let delivery = 0n;
  let other = 0n;
  for (const charge of charges) {
    const value = cents(charge.amount || '0');
    if (charge.kind === 'delivery') delivery += value;
    else {
      if (value > 0n && !charge.label?.trim()) throw new Error('Other fee requires a label');
      other += value;
    }
  }
  return {
    lines,
    subtotal: amount(subtotal),
    lineDiscount: amount(lineDiscount),
    afterLineDiscounts: amount(afterLines),
    globalDiscount: amount(global),
    deliveryCharge: amount(delivery),
    otherFee: amount(other),
    total: amount(afterLines - global + delivery + other),
  };
}

export function amountDueNow(total: string, advance: string): string {
  const due = cents(total) - cents(advance.trim() === '' ? '0' : advance);
  if (due < 0n) throw new Error('Advance cannot exceed the sale total');
  return amount(due);
}

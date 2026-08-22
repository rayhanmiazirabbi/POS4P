import { add, allocate, multiply, subtract, type MoneyValue } from '@pharmacy/money';

export type SaleLine = { id: string; productId: string; name: string; quantity: number; unitPrice: MoneyValue; discount: MoneyValue; tax: MoneyValue };
export type Payment = { method: 'cash' | 'bkash' | 'nagad' | 'due'; amount: MoneyValue };
export type SaleTotals = { subtotal: MoneyValue; discount: MoneyValue; tax: MoneyValue; total: MoneyValue; paid: MoneyValue; due: MoneyValue };
export type SaleSnapshot = { id: string; customerId: string | null; lines: readonly SaleLine[]; totals: SaleTotals; createdAt: string };
export type ReturnRequest = { saleId: string; lines: readonly { saleLineId: string; quantity: number }[] };

function immutableMoney(value: MoneyValue): MoneyValue { return Object.freeze({ ...value }); }

export function calculateSaleTotals(lines: readonly SaleLine[], payments: readonly Payment[] = []): SaleTotals {
  const subtotal = add(...lines.map((line) => multiply(line.unitPrice, line.quantity)));
  const discount = add(...lines.map((line) => line.discount));
  const tax = add(...lines.map((line) => line.tax));
  const total = subtract(add(subtotal, tax), discount);
  const paid = add(...payments.map((payment) => payment.amount));
  const due = subtract(total, paid);
  return { subtotal, discount, tax, total, paid, due };
}

/** Spread an order-level discount over lines weighted by what each line costs, so
 *  the parts sum exactly to `discount` and no line absorbs a rounding ghost. */
export function allocateLineDiscounts(lines: readonly SaleLine[], discount: MoneyValue): MoneyValue[] {
  const weights = lines.map((line) => Number(multiply(line.unitPrice, line.quantity).amount.replace('.', '')));
  return allocate(discount, weights);
}

export function createSaleSnapshot(input: Omit<SaleSnapshot, 'totals'> & { payments?: readonly Payment[] }): SaleSnapshot {
  const lines = input.lines.map((line) => Object.freeze({ ...line, unitPrice: immutableMoney(line.unitPrice), discount: immutableMoney(line.discount), tax: immutableMoney(line.tax) }));
  const calculated = calculateSaleTotals(lines, input.payments ?? []);
  const totals = Object.freeze({ subtotal: immutableMoney(calculated.subtotal), discount: immutableMoney(calculated.discount), tax: immutableMoney(calculated.tax), total: immutableMoney(calculated.total), paid: immutableMoney(calculated.paid), due: immutableMoney(calculated.due) });
  return Object.freeze({ id: input.id, customerId: input.customerId, lines: Object.freeze(lines), totals, createdAt: input.createdAt });
}

export function validateReturn(request: ReturnRequest, original: SaleSnapshot, alreadyReturned: Readonly<Record<string, number>> = {}): void {
  for (const line of request.lines) {
    const originalLine = original.lines.find((item) => item.id === line.saleLineId);
    if (!originalLine) throw new Error(`Unknown sale line: ${line.saleLineId}`);
    if (!Number.isInteger(line.quantity) || line.quantity <= 0 || line.quantity + (alreadyReturned[line.saleLineId] ?? 0) > originalLine.quantity) throw new Error('Return quantity exceeds remaining quantity');
  }
}

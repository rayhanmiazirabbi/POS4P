import { add, allocate, compare, isNegative, isZero, money, multiply, round, subtract, tryMoney, type MoneyValue } from '@pharmacy/money';
import type { PaymentMethod } from '@pharmacy/types';

export type SaleLine = { id: string; productId: string; name: string; quantity: number; unitPrice: MoneyValue; discount: MoneyValue; tax: MoneyValue };

/** Re-exported from `@pharmacy/types` so a tender this package will happily build
 *  cannot be one the backend enum refuses. */
export type { PaymentMethod };

/**
 * One tender against a sale, mirroring `PaymentInput` in
 * `backend/app/schemas/sales.py`.
 *
 * `receivedAmount` is what crossed the counter, not what the sale consumed: the
 * drawer reconciles against the note that was taken, and the server derives the
 * change from the difference. It is required on cash and refused elsewhere --
 * `validateSalePayments` holds both rules.
 */
export type Payment = { method: PaymentMethod; amount: MoneyValue; receivedAmount?: MoneyValue; providerReference?: string };

/** The wallet tenders, as distinct from cash (which takes change) and due (which
 *  needs a customer). */
export type DigitalMethod = Extract<PaymentMethod, 'bkash' | 'nagad'>;

/** `paid` is every tender including `due`, so `due` here is the unpaid remainder
 *  of the cart -- not the balance booked against a customer's account. A sale is
 *  only postable when it is zero; see `validateSalePayments`. */
export type SaleTotals = { subtotal: MoneyValue; discount: MoneyValue; tax: MoneyValue; total: MoneyValue; paid: MoneyValue; due: MoneyValue };
export type SaleSnapshot = { id: string; customerId: string | null; lines: readonly SaleLine[]; totals: SaleTotals; createdAt: string };
export type ReturnRequest = { saleId: string; lines: readonly { saleLineId: string; quantity: number }[] };

const ZERO = money('0.00');

function immutableMoney(value: MoneyValue): MoneyValue { return Object.freeze({ ...value }); }

/** Deep-frozen totals. Freezing only the container leaves every amount inside it
 *  writable, which is the freeze that looks like one and is not. */
function immutableTotals(totals: SaleTotals): SaleTotals {
  return Object.freeze({
    subtotal: immutableMoney(totals.subtotal), discount: immutableMoney(totals.discount), tax: immutableMoney(totals.tax),
    total: immutableMoney(totals.total), paid: immutableMoney(totals.paid), due: immutableMoney(totals.due),
  });
}

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
  return Object.freeze({ id: input.id, customerId: input.customerId, lines: Object.freeze(lines), totals: immutableTotals(calculated), createdAt: input.createdAt });
}

export function validateReturn(request: ReturnRequest, original: SaleSnapshot, alreadyReturned: Readonly<Record<string, number>> = {}): void {
  for (const line of request.lines) {
    const originalLine = original.lines.find((item) => item.id === line.saleLineId);
    if (!originalLine) throw new Error(`Unknown sale line: ${line.saleLineId}`);
    if (!Number.isInteger(line.quantity) || line.quantity <= 0 || line.quantity + (alreadyReturned[line.saleLineId] ?? 0) > originalLine.quantity) throw new Error('Return quantity exceeds remaining quantity');
  }
}

// --- payment split -----------------------------------------------------------

/**
 * Read a tender field, keeping "not a number" out of the money path entirely.
 *
 * `null` means unreadable, and callers must block the sale rather than guess.
 * Coercing bad input to zero is the tempting shortcut and the worst option: the
 * mistyped tender vanishes silently and the sale posts for money the till never
 * took, with the balance landing on cash or on the customer's due account.
 *
 * A negative is `null` too -- a leading minus in a tender box is a typo, not a
 * refund, and honouring it would post a sale that pays out at the counter.
 *
 * Blank is `null` here on purpose. It is not this function's business: an
 * untouched cash box means "the exact total", an untouched digital box means
 * "that tender was not used", and only the caller knows which field it holds.
 */
export function readTender(raw: string): MoneyValue | null {
  const parsed = tryMoney(raw);
  if (parsed === null || isNegative(parsed)) return null;
  // Normalised so a cashier who types "40" gets "40.00" on screen and in the
  // posted body, like every other amount in the app. Nothing is really rounded:
  // `tryMoney` has already refused anything finer than two places, so `round` is
  // just the package's formatter.
  return round(parsed.amount, 'half-up');
}

export function minMoney(a: MoneyValue, b: MoneyValue): MoneyValue {
  return compare(a, b) <= 0 ? a : b;
}

export type TenderSplit = {
  /** Cash charged to the sale, capped at what is still owed after the digital tender. */
  cash: string;
  digital: string;
  /** The remainder, booked against the customer's account. Never negative. */
  due: string;
  /** Handed back to the customer. Never negative. */
  change: string;
  /** False when a field could not be parsed; the caller must not post the sale. */
  readable: boolean;
};

/**
 * Apportion a sale total across the cash and digital tenders, with the remainder
 * falling to the customer's due account.
 *
 * Each tender is clamped to what is still owed, because the fields are edited
 * independently and nothing else stops a digital amount above the total from
 * driving the other lines negative.
 *
 * Returns a split even when a field is unreadable, so the caller can render
 * during a keystroke without a `money` exception blanking the screen. The
 * `readable` flag, not the amounts, is what gates posting.
 */
export function splitTender(totalAmount: string, cashRaw: string, digitalRaw: string): TenderSplit {
  const total = money(totalAmount);
  const cash = cashRaw.trim() === '' ? total : readTender(cashRaw);
  const digital = digitalRaw.trim() === '' ? ZERO : readTender(digitalRaw);

  const digitalApplied = digital === null ? ZERO : minMoney(digital, total);
  const cashApplied = cash === null ? ZERO : minMoney(cash, subtract(total, digitalApplied));
  return {
    cash: cashApplied.amount,
    digital: digitalApplied.amount,
    due: subtract(subtract(total, cashApplied), digitalApplied).amount,
    // Non-negative by construction: `cashApplied` is capped at what was tendered,
    // so the difference is the overpayment or nothing.
    change: cash === null ? ZERO.amount : subtract(cash, cashApplied).amount,
    readable: cash !== null && digital !== null,
  };
}

/**
 * Turn a split into the payment rows a sale is posted with.
 *
 * A tender of nothing is left out rather than sent as `0.00`: the server writes a
 * `payments` row per tender, and a zero bkash line is a payment that never
 * happened sitting in the day's mix. The one exception is a cart that totals
 * nothing, where a zero cash tender is the truth and the API still requires at
 * least one row.
 *
 * The cash line carries what was handed over -- charged plus change -- so the
 * drawer reconciles against the note taken. Note that a split can put change on a
 * *zero* cash line (a cashier who types cash and then a digital amount covering
 * the whole total): the money goes straight back, so no cash row is written and
 * the change is only ever displayed.
 *
 * Callers must still run `validateSalePayments`. A `due` line is well-formed here
 * and invalid on a guest sale, which only the caller knows.
 */
export function tenderPayments(split: TenderSplit, digitalMethod: DigitalMethod): Payment[] {
  const payments: Payment[] = [];
  const cash = money(split.cash);
  if (!isZero(cash)) payments.push({ method: 'cash', amount: cash, receivedAmount: add(cash, money(split.change)) });
  const digital = money(split.digital);
  if (!isZero(digital)) payments.push({ method: digitalMethod, amount: digital });
  const dueAmount = money(split.due);
  if (!isZero(dueAmount)) payments.push({ method: 'due', amount: dueAmount });
  if (payments.length === 0) payments.push({ method: 'cash', amount: ZERO, receivedAmount: ZERO });
  return payments;
}

export type SalePaymentContext = { hasCustomer: boolean };
/**
 * Check a tender set against the rules the server enforces, before the sale is
 * posted.
 *
 * Every message mirrors a specific refusal in `create_sale_payment` and
 * `create_sale` (`backend/app/services/payments.py`, `.../sales.py`). The point is
 * not to trust the client -- the server recomputes and re-checks all of it -- but
 * that a sale refused after the fact is a cart the counter has already cleared
 * and a customer already walking away. Failing here keeps the round trip, and the
 * failed-sale bookkeeping, out of it.
 *
 * Exact parity with the total is required because the server requires it. That
 * also means `calculateSaleTotals(...).due` must be zero at post time: an unpaid
 * remainder is either a `due` tender against a customer or an incomplete sale,
 * never something to post and reconcile later.
 */
export function validateSalePayments(payments: readonly Payment[], total: MoneyValue, context: SalePaymentContext): void {
  if (payments.length === 0) throw new Error('A sale needs at least one payment');
  for (const payment of payments) {
    if (isNegative(payment.amount)) throw new Error('Payment amount cannot be negative');
    if (payment.method === 'cash') {
      if (payment.receivedAmount === undefined) throw new Error('Cash payments require a received amount');
      if (compare(payment.receivedAmount, payment.amount) < 0) {
        throw new Error(`Cash received is short by ৳${subtract(payment.amount, payment.receivedAmount).amount}`);
      }
    } else if (payment.receivedAmount !== undefined && compare(payment.receivedAmount, payment.amount) !== 0) {
      // Change is a cash concept. A wallet transfer of one amount that "received"
      // another is a mistyped field, and the server refuses it outright.
      throw new Error('Only cash payments may carry change');
    }
    if (payment.method === 'due' && !context.hasCustomer) {
      throw new Error('Due payments require a customer on the sale');
    }
  }
  const paid = add(...payments.map((payment) => payment.amount));
  if (compare(paid, total) !== 0) {
    throw new Error(`Payments add up to ৳${paid.amount}, not the sale total of ৳${total.amount}`);
  }
}

/**
 * A tender as the wire carries it: plain decimal strings, optional keys omitted
 * rather than sent as null.
 *
 * Structural rather than an import of `SalePaymentInput`, because this package
 * does not depend on `@pharmacy/api` -- and does not need to. Here so the three
 * shells stop each writing their own mapping: getting `receivedAmount` wrong is a
 * `Conflict` at the counter, and `exactOptionalPropertyTypes` makes the naive
 * spread (`receivedAmount: payment.receivedAmount?.amount`) a type error one shell
 * at a time.
 */
export type WirePayment = { method: PaymentMethod; amount: string; receivedAmount?: string; providerReference?: string };

export function wirePayments(payments: readonly Payment[]): WirePayment[] {
  return payments.map((payment) => ({
    method: payment.method,
    amount: payment.amount.amount,
    ...(payment.receivedAmount === undefined ? {} : { receivedAmount: payment.receivedAmount.amount }),
    ...(payment.providerReference === undefined ? {} : { providerReference: payment.providerReference }),
  }));
}

// --- receipt -----------------------------------------------------------------

export type ReceiptLine = { name: string; quantity: string; unitPrice: MoneyValue; lineTotal: MoneyValue };

/**
 * What the customer is handed, whether or not the sale has reached the server.
 *
 * `receiptNumber` and `saleId` are `null` for a sale sitting in the offline
 * outbox: both are the server's to assign (`_next_receipt_number` locks a
 * per-store counter precisely so two tills cannot print the same number). A
 * client that invented one would hand out a number that later belongs to a
 * different sale, so the slip says so instead -- `receiptNumber === null` is the
 * signal that this is a provisional record, not a filed one.
 */
export type Receipt = {
  receiptNumber: string | null;
  saleId: string | null;
  organizationName: string;
  storeName: string;
  issuedAt: string;
  customerName: string | null;
  lines: readonly ReceiptLine[];
  totals: SaleTotals;
  payments: readonly Payment[];
  /** Cash handed back across the cash tenders. */
  change: MoneyValue;
};

/** The shop details on the slip, which come from the session rather than the sale. */
export type ReceiptHeader = { organizationName: string; storeName: string; customerName?: string | null };

/**
 * A filed sale, as `POST /sales` answers with it.
 *
 * Structural rather than an import of `@pharmacy/api`'s `Sale`, because this
 * package does not depend on the client -- and does not need to. Only the fields a
 * receipt prints are named.
 */
export type FiledSale = {
  id: string;
  receiptNumber?: string | null;
  createdAt: string;
  subtotal: string;
  discount: string;
  total: string;
  items: readonly { productName: string; quantity: string; unitPrice: string; lineTotal: string }[];
  payments: readonly { method: PaymentMethod; amount: string; receivedAmount?: string | null }[];
};

/** Cash returned to the customer: received minus charged, cash tenders only. */
export function cashChange(payments: readonly Payment[]): MoneyValue {
  return add(
    ...payments
      .filter((payment) => payment.method === 'cash' && payment.receivedAmount !== undefined)
      .map((payment) => subtract(payment.receivedAmount as MoneyValue, payment.amount)),
  );
}

function frozenReceipt(input: Omit<Receipt, 'change'>): Receipt {
  return Object.freeze({
    ...input,
    lines: Object.freeze(input.lines.map((line) => Object.freeze({ ...line, unitPrice: immutableMoney(line.unitPrice), lineTotal: immutableMoney(line.lineTotal) }))),
    totals: immutableTotals(input.totals),
    payments: Object.freeze(input.payments.map((payment) => Object.freeze({ ...payment }))),
    change: immutableMoney(cashChange(input.payments)),
  });
}

/**
 * The receipt for a sale the server has filed.
 *
 * Every figure is the server's, not the cart's. The two can differ: the client
 * echoes its display totals and the server ignores them, recomputing from
 * `store_products.sale_price` -- so a price changed since the shelf was loaded is
 * charged at the new one. Printing the cart's arithmetic would hand the customer a
 * slip that disagrees with what came out of their pocket.
 */
export function receiptFromSale(sale: FiledSale, header: ReceiptHeader): Receipt {
  const payments = sale.payments.map<Payment>((payment) => ({
    method: payment.method,
    amount: money(payment.amount),
    ...(payment.receivedAmount === undefined || payment.receivedAmount === null ? {} : { receivedAmount: money(payment.receivedAmount) }),
  }));
  const total = money(sale.total);
  const paid = add(...payments.map((payment) => payment.amount));
  return frozenReceipt({
    receiptNumber: sale.receiptNumber ?? null,
    saleId: sale.id,
    organizationName: header.organizationName,
    storeName: header.storeName,
    issuedAt: sale.createdAt,
    customerName: header.customerName ?? null,
    lines: sale.items.map((item) => ({ name: item.productName, quantity: item.quantity, unitPrice: money(item.unitPrice), lineTotal: money(item.lineTotal) })),
    // No tax column exists server-side yet, so the slip does not invent one.
    totals: { subtotal: money(sale.subtotal), discount: money(sale.discount), tax: ZERO, total, paid, due: subtract(total, paid) },
    payments,
  });
}

/**
 * The receipt for a sale that is still only in the outbox.
 *
 * The counter cannot tell a customer to come back for their slip when the
 * internet returns, so this prints from the cart -- with no receipt number,
 * because that number is not this client's to give out.
 */
export function provisionalReceipt(input: ReceiptHeader & { lines: readonly SaleLine[]; payments: readonly Payment[]; issuedAt: string }): Receipt {
  return frozenReceipt({
    receiptNumber: null,
    saleId: null,
    organizationName: input.organizationName,
    storeName: input.storeName,
    issuedAt: input.issuedAt,
    customerName: input.customerName ?? null,
    lines: input.lines.map((line) => ({ name: line.name, quantity: String(line.quantity), unitPrice: line.unitPrice, lineTotal: multiply(line.unitPrice, line.quantity) })),
    totals: calculateSaleTotals(input.lines, input.payments),
    payments: input.payments,
  });
}

/**
 * The receipt as a thermal printer takes it: plain lines, no markup.
 *
 * Here rather than in a shell because both the desktop till (which prints through
 * `hardware.printReceipt`) and the browser (`window.print`) need the same slip,
 * and because an offline sale has to be printable at all.
 */
export function formatReceiptText(receipt: Receipt): string {
  const lines = [
    receipt.organizationName,
    receipt.storeName,
    receipt.receiptNumber === null ? 'RECEIPT PENDING UPLOAD' : `Receipt ${receipt.receiptNumber}`,
    receipt.issuedAt,
    ...(receipt.customerName === null ? [] : [receipt.customerName]),
    '',
    ...receipt.lines.map((line) => `${line.name} x${line.quantity}  ৳${line.lineTotal.amount}`),
    '',
  ];
  if (!isZero(receipt.totals.discount)) lines.push(`DISCOUNT ৳${receipt.totals.discount.amount}`);
  if (!isZero(receipt.totals.tax)) lines.push(`TAX ৳${receipt.totals.tax.amount}`);
  lines.push(`TOTAL ৳${receipt.totals.total.amount}`);
  lines.push(...receipt.payments.map((payment) => `${payment.method.toUpperCase()} ৳${payment.amount.amount}`));
  if (!isZero(receipt.change)) lines.push(`CHANGE ৳${receipt.change.amount}`);
  return lines.join('\n');
}

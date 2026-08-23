export type RoundingMode = 'half-up' | 'half-even' | 'down' | 'up';
export type MoneyValue = { amount: string; currency: 'BDT' };

const CENTS = 100n;
const TAKA_SIGN = '৳'; // ৳

function parse(value: string): bigint {
  if (!/^-?\d+(\.\d{1,2})?$/.test(value)) throw new Error('Invalid decimal');
  const [whole, fraction = ''] = value.split('.');
  return BigInt(`${whole}${fraction.padEnd(2, '0').slice(0, 2)}`);
}
function format(cents: bigint): string {
  const sign = cents < 0n ? '-' : '';
  const absolute = cents < 0n ? -cents : cents;
  return `${sign}${absolute / CENTS}.${(absolute % CENTS).toString().padStart(2, '0')}`;
}
function assertSameCurrency(a: MoneyValue, b: MoneyValue): void {
  if (a.currency !== b.currency) throw new Error('Currency mismatch');
}

/** Parse any decimal scale so `round` can accept input finer than the 2dp money scale. */
function parseScaled(value: string): { scaled: bigint; scale: bigint } {
  if (!/^-?\d+(\.\d+)?$/.test(value)) throw new Error('Invalid decimal');
  const negative = value.startsWith('-');
  const [whole, fraction = ''] = value.replace('-', '').split('.');
  const scaled = BigInt(whole + fraction);
  return { scaled: negative ? -scaled : scaled, scale: BigInt(fraction.length) };
}

function toCents({ scaled, scale }: { scaled: bigint; scale: bigint }, mode: RoundingMode): bigint {
  if (scale <= 2n) return scaled * CENTS / 10n ** scale;
  const divisor = 10n ** (scale - 2n);
  const quotient = scaled / divisor; // BigInt division truncates toward zero
  const remainder = scaled % divisor;
  if (remainder === 0n) return quotient;
  const magnitude = remainder < 0n ? -remainder : remainder;
  const half = divisor / 2n;
  const away = quotient >= 0n ? quotient + 1n : quotient - 1n;
  if (mode === 'down') return quotient;
  if (mode === 'up') return away;
  if (mode === 'half-up') return magnitude >= half ? away : quotient;
  // half-even: an exact tie moves to the even neighbour, so repeated allocation
  // across many lines does not accumulate half-up's upward bias.
  if (magnitude > half || (magnitude === half && quotient % 2n !== 0n)) return away;
  return quotient;
}

export function money(amount: string, currency: 'BDT' = 'BDT'): MoneyValue {
  parse(amount);
  return { amount, currency };
}

/**
 * Parse an amount that came from a human, yielding `null` instead of throwing
 * when it is not one.
 *
 * `money` throwing is right for values the program computed, and wrong for a text
 * field read on every keystroke: a half-typed "12." would take the render down
 * with it. The tempting shortcut is to coerce whatever will not parse to zero,
 * and that is worse than either -- a mistyped tender then vanishes silently and
 * the sale posts for money the till never took. `null` forces the caller to
 * decide, which is the point.
 */
export function tryMoney(amount: string, currency: 'BDT' = 'BDT'): MoneyValue | null {
  const trimmed = amount.trim();
  try {
    parse(trimmed);
  } catch {
    return null;
  }
  return { amount: trimmed, currency };
}

export function round(value: string, mode: RoundingMode): MoneyValue {
  return money(format(toCents(parseScaled(value), mode)));
}

export function add(...values: MoneyValue[]): MoneyValue {
  if (values.length === 0) return money('0.00');
  const first = values[0];
  if (!first) return money('0.00');
  const rest = values.slice(1);
  const currency = first.currency;
  if (rest.some((value) => value.currency !== currency)) throw new Error('Currency mismatch');
  return money(format(values.reduce((total, value) => total + parse(value.amount), 0n)), currency);
}

export function subtract(a: MoneyValue, b: MoneyValue): MoneyValue {
  assertSameCurrency(a, b);
  return money(format(parse(a.amount) - parse(b.amount)), a.currency);
}

export function multiply(value: MoneyValue, quantity: number): MoneyValue {
  if (!Number.isInteger(quantity) || quantity < 0) throw new Error('Quantity must be a non-negative integer');
  return money(format(parse(value.amount) * BigInt(quantity)), value.currency);
}

export function compare(a: MoneyValue, b: MoneyValue): -1 | 0 | 1 {
  assertSameCurrency(a, b);
  const difference = parse(a.amount) - parse(b.amount);
  return difference < 0n ? -1 : difference > 0n ? 1 : 0;
}

export function isZero(value: MoneyValue): boolean { return parse(value.amount) === 0n; }
export function isNegative(value: MoneyValue): boolean { return parse(value.amount) < 0n; }

export function due(total: MoneyValue, paid: MoneyValue): MoneyValue { return subtract(total, paid); }
export function change(total: MoneyValue, paid: MoneyValue): MoneyValue { return subtract(paid, total); }

/**
 * Split `total` across weights so the parts always sum exactly to `total`.
 * Each part gets its floored share, then the lost cents are handed out one at a
 * time to the positive-weight parts in index order. Backs penny-perfect discount
 * allocation and split tenders; the alternative -- rounding each part
 * independently -- leaks or invents cents at the seams.
 */
export function allocate(total: MoneyValue, weights: readonly number[]): MoneyValue[] {
  if (weights.length === 0) throw new Error('At least one weight is required');
  if (weights.some((weight) => !Number.isFinite(weight) || weight < 0)) throw new Error('Weights must be non-negative');
  if (isNegative(total)) throw new Error('Cannot allocate a negative total');
  const WEIGHT_SCALE = 1_000_000_000n;
  const scaled = weights.map((weight) => BigInt(Math.round(weight * 1e9)));
  const scaledSum = scaled.reduce((sum, weight) => sum + weight, 0n);
  if (scaledSum === 0n) throw new Error('Weights must not all be zero');

  const totalCents = parse(total.amount);
  const eligible = weights.map((weight, index) => weight > 0 && scaled[index] !== 0n);
  let remaining = totalCents;
  const parts = scaled.map((weight) => {
    const share = totalCents * weight / scaledSum;
    remaining -= share;
    return share;
  });
  let cursor = 0;
  while (remaining > 0n) {
    while (!eligible[cursor % parts.length]) cursor += 1;
    parts[cursor % parts.length]! += 1n;
    remaining -= 1n;
    cursor += 1;
  }
  return parts.map((part) => money(format(part), total.currency));
}

export function formatMoney(value: MoneyValue): string {
  const sign = value.amount.startsWith('-') ? '-' : '';
  const [whole = '0', fraction = '00'] = value.amount.replace('-', '').split('.');
  const grouped = whole.replace(/\B(?=(\d{3})+(?!\d))/g, ',');
  return `${sign}${TAKA_SIGN}${grouped}.${fraction}`;
}

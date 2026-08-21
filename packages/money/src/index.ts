export type RoundingMode = 'half-up' | 'half-even' | 'down' | 'up';
export type MoneyValue = { amount: string; currency: 'BDT' };

function parse(value: string): bigint {
  if (!/^-?\d+(\.\d{1,2})?$/.test(value)) throw new Error('Invalid decimal');
  const [whole, fraction = ''] = value.split('.');
  return BigInt(`${whole}${fraction.padEnd(2, '0').slice(0, 2)}`);
}
function format(cents: bigint): string {
  const sign = cents < 0n ? '-' : '';
  const absolute = cents < 0n ? -cents : cents;
  return `${sign}${absolute / 100n}.${(absolute % 100n).toString().padStart(2, '0')}`;
}

export function money(amount: string, currency: 'BDT' = 'BDT'): MoneyValue {
  parse(amount);
  return { amount, currency };
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

export function multiply(value: MoneyValue, quantity: number): MoneyValue {
  if (!Number.isInteger(quantity) || quantity < 0) throw new Error('Quantity must be a non-negative integer');
  return money(format(parse(value.amount) * BigInt(quantity)), value.currency);
}

export function due(total: MoneyValue, paid: MoneyValue): MoneyValue {
  return add(total, money(format(-parse(paid.amount)), paid.currency));
}

export function change(total: MoneyValue, paid: MoneyValue): MoneyValue {
  const result = due(total, paid);
  return money(format(-parse(result.amount)), result.currency);
}

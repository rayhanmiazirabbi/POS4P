import { normalizePhone } from '@pharmacy/core';

export type Customer = { id: string; organizationId: string; displayName: string; phone: string; status: 'active' | 'inactive'; };
export type CustomerProfile = Customer & { email?: string; address?: string; notes?: string; createdAt?: string };
export type CustomerSummary = { customerId: string; due: string; saleCount: number; lastSaleAt: string | null };
export type DueBalanceSummary = { customerId: string | null; dueCents: bigint; saleCount: number; oldestDueAt: string | null };
export type CustomerAssociation = { customerId: string | null };
export type SaleAssociationInput = { saleId: string; customerId: string | null; due: string; createdAt: string };

const SUBSCRIBER_PATTERN = /^1[3-9]\d{8}$/;
const DIALING_PREFIXES = ['+880', '880'];

function toSubscriberDigits(phone: string): string {
  const compact = phone.trim().replace(/[\s()-]/g, '');
  const withCountry = compact.startsWith('+') ? `+${compact.slice(1).replace(/\D/g, '')}` : compact.replace(/\D/g, '');
  let local = withCountry;
  for (const prefix of DIALING_PREFIXES) {
    if (withCountry.startsWith(prefix)) { local = withCountry.slice(prefix.length); break; }
  }
  return local.startsWith('0') ? local.slice(1) : local;
}

/** Normalize any dialing form (`01712...`, `+8801712...`, `8801712...`) to
 *  E.164. Throws when the result is not a Bangladeshi mobile number. */
export function normalizeCustomerPhone(phone: string): string {
  const subscriber = toSubscriberDigits(phone);
  if (!SUBSCRIBER_PATTERN.test(subscriber)) throw new Error(`Invalid Bangladeshi mobile number: ${phone}`);
  return `+880${subscriber}`;
}

/** Non-throwing variant for search boxes where partial input is expected. */
export function parseCustomerPhone(phone: string): string | null {
  try { return normalizeCustomerPhone(phone); } catch { return null; }
}

export function isValidCustomerPhone(phone: string): boolean { return parseCustomerPhone(phone) !== null; }

/** Duplicate detection key: two customers are "the same person" exactly when
 *  their normalized phones collide. */
export function customerPhoneKey(phone: string): string { return normalizeCustomerPhone(phone); }

export function findCustomerByPhone(customers: readonly Customer[], phone: string): Customer | undefined {
  const normalized = customerPhoneKey(phone);
  return customers.find((customer) => customer.phone === normalized && customer.status === 'active');
}

export function findDuplicatePhones(customers: readonly Customer[]): Map<string, Customer[]> {
  const byPhone = new Map<string, Customer[]>();
  for (const customer of customers) {
    const key = customerPhoneKey(customer.phone);
    const existing = byPhone.get(key);
    if (existing) existing.push(customer);
    else byPhone.set(key, [customer]);
  }
  for (const [key, group] of byPhone) if (group.length < 2) byPhone.delete(key);
  return byPhone;
}

export type CustomerSearchFilter = {
  query?: string;
  phone?: string;
  status?: 'active' | 'inactive';
  hasDue?: boolean;
};

/** Build a structured filter from raw UI input; blank queries match everything. */
export function buildCustomerSearchFilter(input: { query?: string; status?: 'active' | 'inactive'; hasDue?: boolean } = {}): CustomerSearchFilter {
  const filter: CustomerSearchFilter = {};
  if (input.status !== undefined) filter.status = input.status;
  if (input.hasDue !== undefined) filter.hasDue = input.hasDue;
  const query = input.query?.trim();
  if (!query) return filter;
  const phoneCandidate = query.replace(/[\s()-]/g, '');
  if (/^\+?\d{6,}$/.test(phoneCandidate)) {
    const phone = parseCustomerPhone(phoneCandidate);
    if (phone) return { ...filter, phone };
  }
  filter.query = query;
  return filter;
}

export function applyCustomerSearchFilter(customers: readonly Customer[], filter: CustomerSearchFilter): Customer[] {
  return customers.filter((customer) => {
    if (filter.status !== undefined && customer.status !== filter.status) return false;
    if (filter.phone !== undefined && customer.phone !== filter.phone) return false;
    if (filter.query !== undefined && !matchesQuery(customer, filter.query)) return false;
    return true;
  });
}

function matchesQuery(customer: Customer, query: string): boolean {
  if (customer.displayName.toLowerCase().includes(query.toLowerCase())) return true;
  const digits = query.replace(/\D/g, '');
  return digits.length > 0 && customer.phone.includes(digits);
}

/** A missing/null customer id is a guest, never an error. */
export function associateCustomer(customerId?: string | null): CustomerAssociation { return { customerId: customerId ?? null }; }
export function isGuestSale(association: Pick<CustomerAssociation, 'customerId'>): boolean { return association.customerId === null; }
export function resolveCustomerId(customerId?: string | null): string | null { return customerId ?? null; }

function toCents(amount: string): bigint {
  if (!/^-?\d+(\.\d{1,2})?$/.test(amount)) throw new Error(`Invalid decimal: ${amount}`);
  const negative = amount.startsWith('-');
  const [whole = '0', fraction = ''] = amount.replace('-', '').split('.');
  const cents = BigInt(whole) * 100n + BigInt(fraction.padEnd(2, '0').slice(0, 2));
  return negative ? -cents : cents;
}

function formatCents(cents: bigint): string {
  const sign = cents < 0n ? '-' : '';
  const absolute = cents < 0n ? -cents : cents;
  return `${sign}${absolute / 100n}.${(absolute % 100n).toString().padStart(2, '0')}`;
}

export function summarizeCustomer(customerId: string, sales: readonly SaleAssociationInput[]): CustomerSummary {
  const customerSales = sales.filter((sale) => sale.customerId === customerId);
  const dueCents = customerSales.reduce((total, sale) => total + toCents(sale.due), 0n);
  return { customerId, due: formatCents(dueCents), saleCount: customerSales.length, lastSaleAt: customerSales.map((sale) => sale.createdAt).sort().at(-1) ?? null };
}

/** Aggregate dues across all sales; guests roll up under a `null` id so a store's
 *  outstanding balance is complete without forcing customer accounts. */
export function dueBalanceSummaries(sales: readonly SaleAssociationInput[]): DueBalanceSummary[] {
  const buckets = new Map<string, { dueCents: bigint; count: number; oldestDueAt: string | null }>();
  for (const sale of sales) {
    const key = sale.customerId ?? '';
    const bucket = buckets.get(key) ?? { dueCents: 0n, count: 0, oldestDueAt: null };
    bucket.dueCents += toCents(sale.due);
    bucket.count += 1;
    if (bucket.oldestDueAt === null || sale.createdAt < bucket.oldestDueAt) bucket.oldestDueAt = sale.createdAt;
    buckets.set(key, bucket);
  }
  return [...buckets.entries()]
    .map(([key, bucket]) => ({ customerId: key === '' ? null : key, dueCents: bucket.dueCents, saleCount: bucket.count, oldestDueAt: bucket.oldestDueAt }))
    .sort((a, b) => (b.dueCents > a.dueCents ? 1 : b.dueCents < a.dueCents ? -1 : 0) || (a.customerId ?? '').localeCompare(b.customerId ?? ''));
}

export function totalOutstandingDue(sales: readonly SaleAssociationInput[]): bigint {
  return dueBalanceSummaries(sales).reduce((total, summary) => total + summary.dueCents, 0n);
}

export function formatDueBalance(dueCents: bigint): string {
  const formatted = formatCents(dueCents < 0n ? -dueCents : dueCents);
  const sign = dueCents < 0n ? '-' : '';
  return `${sign}৳${formatted}`;
}

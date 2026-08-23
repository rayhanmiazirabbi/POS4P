import { describe, expect, it } from 'vitest';
import {
  applyCustomerSearchFilter,
  associateCustomer,
  buildCustomerSearchFilter,
  customerPhoneKey,
  dueBalanceSummaries,
  findCustomerByPhone,
  findDuplicatePhones,
  formatDueBalance,
  isGuestSale,
  isValidCustomerPhone,
  normalizeCustomerPhone,
  parseCustomerPhone,
  resolveCustomerId,
  summarizeCustomer,
  totalOutstandingDue,
  type Customer,
} from '../src/index';

const customer: Customer = { id: 'c1', organizationId: 'o1', displayName: 'Ayesha', phone: '+8801712345678', status: 'active' };
const inactive: Customer = { id: 'c2', organizationId: 'o1', displayName: 'Rahim', phone: '+8801812345678', status: 'inactive' };
const duplicate: Customer = { id: 'c3', organizationId: 'o1', displayName: 'Ayesha Uddin', phone: '01712345678', status: 'active' };

describe('phone normalization', () => {
  it('accepts every dialing form and strips separators', () => {
    for (const input of ['01712345678', '+8801712345678', '8801712345678', '01712 345678', '01712-345-678', '+880 1712 345678', '(+880)1712345678']) {
      expect(normalizeCustomerPhone(input)).toBe('+8801712345678');
    }
  });

  it('rejects invalid Bangladeshi mobile numbers', () => {
    for (const input of ['01112345678', '0171234567', '017123456789', '1234567890', '+8802012345678', '', 'abc']) {
      expect(() => normalizeCustomerPhone(input)).toThrow();
      expect(isValidCustomerPhone(input)).toBe(false);
    }
  });

  it('parses safely to null instead of throwing on search input', () => {
    expect(parseCustomerPhone('01712345678')).toBe('+8801712345678');
    expect(parseCustomerPhone('not a phone')).toBeNull();
  });

  it('keys duplicates by normalized phone regardless of dialing form', () => {
    expect(customerPhoneKey('01812-345678')).toBe(customerPhoneKey('+8801812345678'));
    const groups = findDuplicatePhones([customer, duplicate, inactive]);
    expect(groups.size).toBe(1);
    expect([...(groups.get('+8801712345678') ?? []).map((entry) => entry.id)]).toEqual(['c1', 'c3']);
    expect(findDuplicatePhones([customer, inactive]).size).toBe(0);
  });
});

describe('search', () => {
  it('finds active customers by phone across dialing forms', () => {
    expect(findCustomerByPhone([customer, inactive], '01712-345678')).toEqual(customer);
    expect(findCustomerByPhone([inactive], '+8801812345678')).toBeUndefined();
    expect(findCustomerByPhone([], '01712345678')).toBeUndefined();
  });

  it('builds structured filters from raw input', () => {
    expect(buildCustomerSearchFilter({ query: '  ' })).toEqual({});
    expect(buildCustomerSearchFilter({ query: '01712 345678' })).toEqual({ phone: '+8801712345678' });
    expect(buildCustomerSearchFilter({ query: 'ayesha' })).toEqual({ query: 'ayesha' });
    expect(buildCustomerSearchFilter({ status: 'active', hasDue: true })).toEqual({ status: 'active', hasDue: true });
  });

  it('applies filters to customers', () => {
    const customers = [customer, duplicate, inactive];
    expect(applyCustomerSearchFilter(customers, { query: 'ayesha' }).map((entry) => entry.id)).toEqual(['c1', 'c3']);
    expect(applyCustomerSearchFilter(customers, { status: 'inactive' }).map((entry) => entry.id)).toEqual(['c2']);
    expect(applyCustomerSearchFilter(customers, { phone: '+8801712345678' })).toEqual([customer]);
    expect(applyCustomerSearchFilter(customers, {})).toHaveLength(3);
  });
});

describe('guest handling', () => {
  it('keeps null customer ids first-class', () => {
    expect(associateCustomer()).toEqual({ customerId: null });
    expect(associateCustomer(null)).toEqual({ customerId: null });
    expect(associateCustomer('c1')).toEqual({ customerId: 'c1' });
    expect(isGuestSale({ customerId: null })).toBe(true);
    expect(isGuestSale(associateCustomer('c1'))).toBe(false);
    expect(resolveCustomerId(undefined)).toBeNull();
    expect(resolveCustomerId('c1')).toBe('c1');
  });
});

describe('due summaries', () => {
  const sales = [
    { saleId: 's1', customerId: 'c1', due: '10.25', createdAt: '2026-01-03T10:00:00Z' },
    { saleId: 's2', customerId: 'c1', due: '5.00', createdAt: '2026-01-05T10:00:00Z' },
    { saleId: 's3', customerId: null, due: '2.50', createdAt: '2026-01-04T10:00:00Z' },
    { saleId: 's4', customerId: null, due: '0.75', createdAt: '2026-01-02T10:00:00Z' },
  ];

  it('summarizes only the identified customer with exact cents', () => {
    expect(summarizeCustomer('c1', sales)).toEqual({ customerId: 'c1', due: '15.25', saleCount: 2, lastSaleAt: '2026-01-05T10:00:00Z' });
    expect(summarizeCustomer('missing', sales).saleCount).toBe(0);
    expect(summarizeCustomer('missing', sales).lastSaleAt).toBeNull();
  });

  it('rolls guest dues under a null id without losing cents', () => {
    const summaries = dueBalanceSummaries(sales);
    expect(summaries.map((summary) => summary.customerId)).toEqual(['c1', null]);
    expect(summaries[1]).toMatchObject({ customerId: null, dueCents: 325n, saleCount: 2, oldestDueAt: '2026-01-02T10:00:00Z' });
  });

  it('aggregates totals exactly in bigint cents', () => {
    expect(totalOutstandingDue(sales)).toBe(1850n);
    expect(totalOutstandingDue([])).toBe(0n);
    expect(formatDueBalance(1850n)).toBe('৳18.50');
    expect(formatDueBalance(-250n)).toBe('-৳2.50');
    expect(formatDueBalance(5n)).toBe('৳0.05');
  });
});

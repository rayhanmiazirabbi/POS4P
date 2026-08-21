import { describe, expect, it } from 'vitest';
import { associateCustomer, findCustomerByPhone, normalizeCustomerPhone, summarizeCustomer, type Customer } from '../src/index';

const customer: Customer = { id: 'c1', organizationId: 'o1', displayName: 'A', phone: '+8801712345678', status: 'active' };
describe('customers', () => {
  it('normalizes phone lookup and keeps guests first-class', () => {
    expect(normalizeCustomerPhone('01712 345678')).toBe(customer.phone);
    expect(findCustomerByPhone([customer], '01712-345678')).toEqual(customer);
    expect(associateCustomer()).toEqual({ customerId: null });
  });
  it('summarizes only identified customer sales', () => { expect(summarizeCustomer('c1', [{ customerId: 'c1', due: '2.50', createdAt: '2026-01-02' }, { customerId: null, due: '4', createdAt: '2026-01-03' }])).toMatchObject({ due: '2.50', saleCount: 1 }); });
});

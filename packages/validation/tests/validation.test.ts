import { describe, expect, it } from 'vitest';
import { batchSchema, customerSchema, paymentSchema, saleSchema, userSchema } from '../src/index';

it('normalizes customer phone numbers', () => {
  expect(customerSchema.parse({ displayName: 'A', phone: '01712 345678' }).phone).toBe('+8801712345678');
});

it('rejects malformed sale and payment inputs', () => {
  expect(() => saleSchema.parse({ lines: [], idempotencyKey: 'short' })).toThrow();
  expect(() => paymentSchema.parse({ method: 'cash', amount: '0' })).toThrow();
});

describe('userSchema parity with the backend StaffRole contract', () => {
  it('accepts every role the create endpoint accepts', () => {
    for (const role of ['manager', 'cashier', 'inventory_staff'] as const) {
      expect(userSchema.parse({ displayName: 'Staff', role, phone: '01700 000001' }).role).toBe(role);
    }
  });
  it('rejects owner, matching the 422 the API would return', () => {
    expect(() => userSchema.parse({ displayName: 'Minted', role: 'owner', phone: '01700 000002' })).toThrow();
  });
});

describe('batchSchema', () => {
  it('accepts a well-formed batch line', () => {
    expect(batchSchema.parse({ batchNumber: ' B-12 ', quantity: '10.5', expiryDate: '2099-01-01' }))
      .toEqual({ batchNumber: 'B-12', quantity: '10.5', expiryDate: '2099-01-01' });
  });
  it('rejects blank batch numbers, zero quantities, and past expiries', () => {
    expect(() => batchSchema.parse({ batchNumber: '  ', quantity: '1', expiryDate: '2099-01-01' })).toThrow();
    expect(() => batchSchema.parse({ batchNumber: 'B', quantity: '0', expiryDate: '2099-01-01' })).toThrow();
    expect(() => batchSchema.parse({ batchNumber: 'B', quantity: '1', expiryDate: '2020-01-01' })).toThrow();
  });
});

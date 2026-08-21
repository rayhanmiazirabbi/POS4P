import { expect, it } from 'vitest';
import { customerSchema, paymentSchema, saleSchema } from '../src/index';

it('normalizes customer phone numbers', () => {
  expect(customerSchema.parse({ displayName: 'A', phone: '01712 345678' }).phone).toBe('+8801712345678');
});

it('rejects malformed sale and payment inputs', () => {
  expect(() => saleSchema.parse({ lines: [], idempotencyKey: 'short' })).toThrow();
  expect(() => paymentSchema.parse({ method: 'cash', amount: '0' })).toThrow();
});

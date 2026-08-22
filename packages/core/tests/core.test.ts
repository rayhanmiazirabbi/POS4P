import { describe, expect, it } from 'vitest';
import {
  addQuantities, assertId, compareQuantities, createId, domainError, isDomainError,
  normalizePhone, nowUtc, parseQuantity,
} from '../src/index';

describe('core primitives', () => {
  it('creates UUIDv7 identifiers', () => {
    const id = createId();
    expect(id).toMatch(/^[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i);
    expect(assertId(id)).toBe(id);
  });

  it('normalizes timestamps and Bangladesh phone numbers', () => {
    expect(nowUtc()).toMatch(/Z$/);
    expect(normalizePhone('01712 345678')).toBe('+8801712345678');
  });

  it('rejects invalid identifiers', () => { expect(() => assertId('nope')).toThrow('Invalid UUID'); });
});

describe('domain errors', () => {
  it('builds and recognises the shared error shape', () => {
    expect(domainError('FORBIDDEN', 'no')).toEqual({ code: 'FORBIDDEN', message: 'no' });
    expect(domainError('CONFLICT', 'clash', { field: 'phone' })).toEqual({
      code: 'CONFLICT', message: 'clash', details: { field: 'phone' },
    });
    expect(isDomainError(domainError('NOT_FOUND', 'missing'))).toBe(true);
    expect(isDomainError(new Error('plain'))).toBe(false);
    expect(isDomainError(null)).toBe(false);
  });
});

describe('quantities', () => {
  it('parses up to 4 decimal places and rejects anything finer or negative', () => {
    expect(parseQuantity('2.5').value).toBe('2.5');
    expect(parseQuantity('0.0001').value).toBe('0.0001');
    expect(() => parseQuantity('1.00001')).toThrow('Invalid quantity');
    expect(() => parseQuantity('-1')).toThrow('Invalid quantity');
  });

  it('compares quantities on value, not string form', () => {
    expect(compareQuantities('10.5', '10.50')).toBe(0);
    expect(compareQuantities('9.9999', '10')).toBe(-1);
    expect(compareQuantities('2.25', '2.2499')).toBe(1);
  });

  it('adds quantities at 4dp like the backend quantity columns', () => {
    expect(addQuantities('0.5', '0.25')).toEqual({ value: '0.75', unit: '' });
    expect(addQuantities('1.0001', '2.0002').value).toBe('3.0003');
    expect(addQuantities('0.0001', '0.0009').value).toBe('0.001');
    expect(() => addQuantities({ value: '1', unit: 'strip' }, { value: '1', unit: 'box' })).toThrow('Unit mismatch');
  });
});


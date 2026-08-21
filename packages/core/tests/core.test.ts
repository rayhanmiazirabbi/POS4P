import { describe, expect, it } from 'vitest';
import { assertId, createId, normalizePhone, nowUtc } from '../src/index';

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

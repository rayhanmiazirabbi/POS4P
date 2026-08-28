import { describe, expect, it } from 'vitest';

import { decimalEntry } from './numeric-input';

describe('decimalEntry', () => {
  it('rejects letters and punctuation while preserving digits', () => {
    expect(decimalEntry('ab12x.3z4')).toBe('12.34');
  });

  it('keeps only one decimal separator', () => {
    expect(decimalEntry('1.2.3')).toBe('1.23');
  });

  it('supports an empty field and a leading decimal', () => {
    expect(decimalEntry('')).toBe('');
    expect(decimalEntry('.5')).toBe('0.5');
  });
});

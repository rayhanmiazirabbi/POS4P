import { describe, expect, it } from 'vitest';

import type { BrowserStorageAdapter } from './dexie';

describe('browser storage boundary', () => {
  it('keeps the browser adapter contract platform-specific', () => {
    const adapter: BrowserStorageAdapter = {
      get: async () => null,
      set: async () => undefined,
      remove: async () => undefined,
    };

    expect(Object.keys(adapter).sort()).toEqual(['get', 'remove', 'set']);
  });
});

import { describe, expect, it } from 'vitest';

import { createDesktopPlatform } from './tauri';

describe('desktop platform boundary', () => {
  it('keeps database and hardware behind injected adapters', () => {
    const platform = {
      database: { get: async () => null, set: async () => undefined, remove: async () => undefined },
      hardware: {
        printReceipt: async () => ({ ok: true as const }),
        scan: async () => null,
        openCashDrawer: async () => ({ ok: true as const }),
      },
    };

    expect(createDesktopPlatform(platform)).toBe(platform);
  });
});

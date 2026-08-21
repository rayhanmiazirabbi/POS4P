import { describe, expect, it } from 'vitest';

import { createMobileStorage } from './storageBoundary';

describe('mobile storage boundary', () => {
  it('routes auth storage through SecureStore', async () => {
    const calls: string[] = [];
    const storage = createMobileStorage({
      sqlite: { get: async () => null, set: async () => undefined, remove: async () => undefined },
      secureStore: {
        getItem: async (key) => { calls.push(`get:${key}`); return null; },
        setItem: async (key) => { calls.push(`set:${key}`); },
        deleteItem: async (key) => { calls.push(`delete:${key}`); },
      },
    });

    await storage.credentials.get('session');
    await storage.credentials.set('session', '{}');
    await storage.credentials.remove('session');

    expect(calls).toEqual(['get:session', 'set:session', 'delete:session']);
    expect(storage.offline).toBeDefined();
  });
});

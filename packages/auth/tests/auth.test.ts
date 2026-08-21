import { expect, it, vi } from 'vitest';
import type { StorageAdapter } from '@pharmacy/api';
import type { Session } from '@pharmacy/types';
import { SessionManager } from '../src/index';

const session = { accessToken: 'access', refreshToken: 'refresh', expiresAt: '2026-01-01T00:00:00.000Z', user: {} } as Session;

it('persists and removes session credentials through the secure adapter', async () => {
  const values = new Map<string, string>();
  const storage: StorageAdapter = {
    get: vi.fn(async (key) => values.get(key) ?? null),
    set: vi.fn(async (key, value) => { values.set(key, value); }),
    remove: vi.fn(async (key) => { values.delete(key); }),
  };
  const manager = new SessionManager({ storage, refresh: vi.fn(async () => session), logout: vi.fn(async () => undefined) });
  await manager.persist(session);
  expect((await manager.restore())?.accessToken).toBe('access');
  await manager.logout();
  expect(await manager.restore()).toBeNull();
});

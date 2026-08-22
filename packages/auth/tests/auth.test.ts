import { describe, expect, it, vi } from 'vitest';
import { createMemoryStorage, storageKeys } from '@pharmacy/api';
import type { Session } from '@pharmacy/types';
import { SessionManager } from '../src/index';

const session: Session = { accessToken: 'access', refreshToken: 'refresh', expiresAt: '2026-01-01T00:00:00.000Z', user: {} } as Session;

function manager(storage = createMemoryStorage()) {
  return {
    storage,
    manager: new SessionManager({ storage, refresh: vi.fn(async () => session), logout: vi.fn(async () => undefined) }),
  };
}

describe('SessionManager', () => {
  it('persists and removes session credentials through the secure adapter', async () => {
    const { storage, manager: instance } = manager();
    await instance.persist(session);
    expect((await instance.restore())?.accessToken).toBe('access');
    await instance.logout();
    expect(await instance.restore()).toBeNull();
  });

  it('uses the storage keys owned by @pharmacy/api', async () => {
    const { storage, manager: instance } = manager();
    await instance.persist(session);
    expect(await storage.get(storageKeys.accessToken)).toBe('access');
    expect(await storage.get(storageKeys.refreshToken)).toBe('refresh');
  });

  it('keeps the refresh token across an app restart', async () => {
    const { storage, manager: first } = manager();
    await first.persist(session);

    // A fresh manager over the same storage models a cold start.
    const { manager: second } = manager(storage);
    const restored = await second.restore();
    expect(restored?.refreshToken).toBe('refresh');
    expect(await storage.get(storageKeys.refreshToken)).toBe('refresh');

    await second.logout();
    expect(await storage.get(storageKeys.refreshToken)).toBeNull();
    expect(await storage.get(storageKeys.session)).toBeNull();
  });

  it('clears corrupt storage rather than throwing', async () => {
    const { storage, manager: instance } = manager();
    await storage.set(storageKeys.session, '{not json');
    expect(await instance.restore()).toBeNull();
    expect(await storage.get(storageKeys.session)).toBeNull();
  });
});

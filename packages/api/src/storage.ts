/**
 * Key/value persistence supplied by each platform (SecureStore on native,
 * IndexedDB on web, the Tauri store on desktop).
 *
 * Kept intentionally minimal: `@pharmacy/auth` and all three app shells already
 * implement this exact shape.
 */
export type StorageAdapter = {
  get(key: string): Promise<string | null>;
  set(key: string, value: string): Promise<void>;
  remove(key: string): Promise<void>;
};

/**
 * Storage keys this package reads. Auth owns writing the token pair.
 *
 * `deviceKey` is deliberately not a session key: it identifies the installation
 * and must survive sign-out, or every shift change registers a new device and
 * restarts its sync sequence stream.
 */
export const storageKeys = { accessToken: 'access_token', refreshToken: 'refresh_token', session: 'session', deviceKey: 'device_key' } as const;

/** In-memory adapter for tests and ephemeral sessions. */
export function createMemoryStorage(initial: Readonly<Record<string, string>> = {}): StorageAdapter {
  const values = new Map<string, string>(Object.entries(initial));
  return {
    get: async (key) => values.get(key) ?? null,
    set: async (key, value) => {
      values.set(key, value);
    },
    remove: async (key) => {
      values.delete(key);
    },
  };
}

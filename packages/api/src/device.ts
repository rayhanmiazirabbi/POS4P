import { createId } from '@pharmacy/core';

import type { DeviceClaim } from './resources';
import type { StorageAdapter } from './storage';
import { storageKeys } from './storage';

/**
 * Random, opaque, and long enough that guessing one is not a strategy.
 *
 * `_resolve_device` in the backend registers an unrecognised key on first sight,
 * so a key is closer to a credential than to a name: a client that presents
 * another terminal's key is treated as that terminal and files its sales into
 * that terminal's stream. `crypto.getRandomValues` is available in every runtime
 * this ships to, but `randomUUID` is secure-context only -- and a counter served
 * over plain HTTP on a shop LAN is exactly where this runs.
 */
function generateDeviceKey(): string {
  const source = globalThis.crypto;
  if (source !== undefined && typeof source.getRandomValues === 'function') {
    const bytes = source.getRandomValues(new Uint8Array(24));
    return Array.from(bytes, (byte) => byte.toString(16).padStart(2, '0')).join('');
  }
  // No Web Crypto at all. uuidv7 is weaker here but still unique per call, which
  // is the property that must not be lost; refusing to log in would be worse.
  return `${createId()}${createId()}`.replace(/-/g, '');
}

export type DeviceIdentity = {
  /** The stored key, or `null` if this install has never had one. */
  peek(): Promise<string | null>;
  /** The claim to attach to a login, generating and persisting the key on first use. */
  claim(deviceName: string): Promise<DeviceClaim>;
  /** Forget the key. Only for "unpair this terminal" -- not for sign-out. */
  forget(): Promise<void>;
};

/**
 * The per-install device identity a login attaches so the server can tell
 * terminals apart.
 *
 * The key belongs to the *installation*, which is why it survives sign-out: two
 * cashiers sharing one till are one device, and one cashier with a phone and a
 * spare is two. Deriving it from the signed-in user -- which the POS screen used
 * to do, as `mobile-${userId.slice(0, 8)}` -- inverts both of those, and the
 * second is the expensive one. Two phones answering to one device id kept two
 * independent client-sequence counters, so both first offline sales carried the
 * same positional idempotency key and the server replayed the first sale's
 * receipt for the second. See `createSyncEnvelope` in `@pharmacy/sync`.
 *
 * It is also not a session: clearing it on logout would register a new device row
 * on every shift change and reset the sequence stream with it.
 */
export function createDeviceIdentity(storage: StorageAdapter): DeviceIdentity {
  let inFlight: Promise<string> | null = null;

  async function ensureKey(): Promise<string> {
    const existing = await storage.get(storageKeys.deviceKey);
    if (existing !== null && existing.trim() !== '') return existing;
    const created = generateDeviceKey();
    await storage.set(storageKeys.deviceKey, created);
    return created;
  }

  return {
    peek: () => storage.get(storageKeys.deviceKey),
    async claim(deviceName) {
      // Serialized: two concurrent logins (a retry racing the first attempt)
      // would otherwise each generate a key, and the loser's device row is
      // orphaned while the sequence stream silently starts over.
      inFlight ??= ensureKey().finally(() => {
        inFlight = null;
      });
      return { deviceKey: await inFlight, deviceName };
    },
    forget: () => storage.remove(storageKeys.deviceKey),
  };
}

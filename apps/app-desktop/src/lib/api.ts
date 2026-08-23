import { ApiClient, createDeviceIdentity, createFetchTransport, createPharmacyApi, type StorageAdapter } from '@pharmacy/api';

import { desktopPlatform } from '../platform/runtime';

const tokenStorage: StorageAdapter = {
  get: async (key) => (await desktopPlatform()).database.get(key),
  set: async (key, value) => {
    await (await desktopPlatform()).database.set(key, value);
  },
  remove: async (key) => {
    await (await desktopPlatform()).database.remove(key);
  },
};

// Vite inlines `import.meta.env.VITE_*` at build time, which is how the other two
// shells are configured (`NEXT_PUBLIC_API_URL`, `EXPO_PUBLIC_API_URL`). The
// previous `globalThis.__API_URL__` was never assigned by anything in the repo --
// no define, no injected script -- so the fallback was not a fallback: every
// packaged desktop till talked to `localhost:8000` on its own machine, with no
// way to point it at the shop server short of editing this file.
const configuredBaseUrl = import.meta.env.VITE_API_URL ?? 'http://localhost:8000';

export const pharmacyApi = createPharmacyApi(
  new ApiClient(createFetchTransport({ fetch: fetch.bind(globalThis) }), tokenStorage, { baseUrl: configuredBaseUrl }),
);

/**
 * This till's device identity, in the same durable store as the tokens.
 *
 * Every login attaches it, which is what lets the server tell two tills apart.
 * Without it the access token carries no `dev` claim, `/sync/events` refuses the
 * upload with `DEVICE_CONTEXT_REQUIRED`, and the offline queue has nowhere to go --
 * so a counter could ring up sales it can never send.
 */
export const deviceIdentity = createDeviceIdentity(tokenStorage);

/** A label for the device list, so a till that needs revoking can be picked out. */
export function deviceName(): string {
  return 'Desktop till';
}

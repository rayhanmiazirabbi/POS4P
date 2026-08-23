'use client';

import { ApiClient, createDeviceIdentity, createFetchTransport, createPharmacyApi } from '@pharmacy/api';

import { dexieStorage } from '../platform/dexie';

/** One shared client: bearer tokens, idempotency, and retries live in `@pharmacy/api`. */
export const pharmacyApi = createPharmacyApi(
  new ApiClient(createFetchTransport({ fetch: fetch.bind(globalThis) }), dexieStorage, {
    baseUrl: process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000',
  }),
);

/**
 * This browser's device identity, stored alongside the tokens.
 *
 * Every login attaches it, which is what lets the server tell two terminals apart.
 * Without it the access token carries no `dev` claim, `/sync/events` refuses the
 * upload with `DEVICE_CONTEXT_REQUIRED`, and the offline queue has nowhere to go --
 * so a counter could ring up sales it can never send.
 */
export const deviceIdentity = createDeviceIdentity(dexieStorage);

/**
 * A label for the device list, so a terminal that needs revoking can be picked out.
 *
 * The browser will not say which machine it is, and the name is cosmetic anyway --
 * device rows are keyed by the generated device key, not by this. What it must not
 * do is claim to know more than it does.
 */
export function deviceName(): string {
  return 'Web counter';
}

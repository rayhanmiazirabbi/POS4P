import { ApiClient, createDeviceIdentity, createFetchTransport, createPharmacyApi, type StorageAdapter } from '@pharmacy/api';
import { Platform } from 'react-native';

import { secureStoreAdapter } from '../platform/nativeAdapters';

/** Tokens live in the device keystore, never in plain SQLite. */
const credentialStorage: StorageAdapter = {
  get: (key) => secureStoreAdapter.getItem(key),
  set: (key, value) => secureStoreAdapter.setItem(key, value),
  remove: (key) => secureStoreAdapter.deleteItem(key),
};

export const pharmacyApi = createPharmacyApi(
  new ApiClient(createFetchTransport({ fetch: fetch.bind(globalThis) }), credentialStorage, {
    baseUrl: process.env.EXPO_PUBLIC_API_URL ?? 'http://localhost:8000',
  }),
);

/**
 * This installation's device identity, in the keystore alongside the tokens.
 *
 * Every login attaches it, which is what lets the server tell two phones apart.
 * Without it `context.device_id` is null, `/sync/events` refuses the upload with
 * `DEVICE_CONTEXT_REQUIRED`, and the offline queue has nowhere to go.
 */
export const deviceIdentity = createDeviceIdentity(credentialStorage);

/** Attached to a login so a revoked terminal is recognisable in the device list. */
export function deviceName(): string {
  return `${Platform.OS === 'ios' ? 'iOS' : 'Android'} counter phone`;
}

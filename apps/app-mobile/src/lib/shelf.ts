import { createShelfStore, type ShelfStore } from '@pharmacy/sync';

import { openMobileDatabase, createSQLiteAdapter } from '../platform/nativeAdapters';

/**
 * The shelf, on the phone.
 *
 * Separate from the outbox blob on purpose. They have opposite failure rules --
 * an unreadable outbox must throw, because it holds sales the server has never
 * seen, while an unreadable shelf is just a fetch away -- and sharing one record
 * would mean a corrupt price list took the sales down with it.
 */
const SHELF_KEY = 'pos_shelf_v1';

let ready: Promise<ShelfStore> | null = null;

export function shelf(): Promise<ShelfStore> {
  ready ??= openMobileDatabase()
    .then(createSQLiteAdapter)
    .then((adapter) =>
      createShelfStore({ read: () => adapter.get(SHELF_KEY), write: (value) => adapter.set(SHELF_KEY, value) }),
    );
  return ready;
}

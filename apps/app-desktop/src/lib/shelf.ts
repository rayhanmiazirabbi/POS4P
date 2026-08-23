import { createShelfStore, type OutboxStorage, type ShelfStore } from '@pharmacy/sync';

import { desktopPlatform } from '../platform/runtime';

/**
 * The shelf, on the till.
 *
 * Its own record, not part of the outbox blob. They have opposite failure rules --
 * an unreadable outbox must throw, because it holds sales the server has never
 * seen, while an unreadable shelf is just a fetch away -- and sharing one record
 * would let a corrupt price list take the sales down with it.
 */
const SHELF_KEY = 'desktop_shelf_v1';

const storage: OutboxStorage = {
  read: async () => (await desktopPlatform()).database.get(SHELF_KEY),
  write: async (value) => {
    await (await desktopPlatform()).database.set(SHELF_KEY, value);
  },
};

export const shelf: ShelfStore = createShelfStore(storage);

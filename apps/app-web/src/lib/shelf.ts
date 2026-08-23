'use client';

import { createShelfStore, type ShelfStore } from '@pharmacy/sync';

import { localRecord } from './database';

/**
 * The shelf, in the browser.
 *
 * Its own record beside the outbox, not inside it. They have opposite failure
 * rules -- an unreadable outbox must throw, because it holds sales the server has
 * never seen, while an unreadable shelf is one fetch away -- so sharing a record
 * would let a corrupt price list take the sales down with it.
 */
const SHELF_KEY = 'shelf_v1';

export const shelf: ShelfStore = createShelfStore(localRecord(SHELF_KEY));

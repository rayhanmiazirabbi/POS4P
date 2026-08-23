'use client';

import Dexie, { type Table } from 'dexie';

import type { SaleCreateRequest } from '@pharmacy/api';
import type { OutboxStorage } from '@pharmacy/sync';

/**
 * A sale queued by the pre-sync implementation: an idempotency key and a body,
 * destined for `POST /sales`.
 *
 * Kept only to be read once. That queue had no device identity, no client
 * sequence, and no per-event acks, so it could not use the sync protocol at all --
 * and it marked a sale permanently rejected on any non-network error, which threw
 * away paid-for sales over an `INSUFFICIENT_STOCK` a delivery would have fixed.
 */
export type LegacySale = { id: string; body: SaleCreateRequest; createdAt: string; rejection?: { reason: string; at: string } };

/**
 * The browser's local store for everything the counter needs offline.
 *
 * One database, declared once. `records` is a plain key-value table because the
 * things that go in it -- the sale outbox, the cached shelf -- are single blobs
 * with their own decoders, and giving each a typed Dexie table would put a schema
 * migration in the way of every change to either.
 */
class PosDatabase extends Dexie {
  readonly queue!: Table<LegacySale, string>;
  readonly outbox!: Table<{ key: string; value: string }, string>;

  constructor() {
    super('pharmacy-pos-queue');
    this.version(1).stores({ queue: 'id, createdAt' });
    this.version(2).stores({ queue: 'id, createdAt, rejection.at' });
    // v3 adds the single blob the shared outbox lives in. `queue` is kept rather
    // than dropped: rows already in it are money taken at the counter, and
    // `adoptLegacySales` has to be able to read them before they are cleared.
    this.version(3).stores({ queue: 'id, createdAt, rejection.at', outbox: 'key' });
  }
}

export const database = new PosDatabase();

/**
 * One record in the key-value table, as the storage port `@pharmacy/sync` expects.
 *
 * Each caller passes its own key and gets an isolated blob. That isolation is the
 * point: the outbox must throw on an unreadable blob, because it holds sales the
 * server has never seen, while the shelf reads a corrupt blob as empty and
 * refetches -- one shared record would let a bad price list discard the sales.
 */
export function localRecord(key: string): OutboxStorage {
  return {
    read: async () => (await database.outbox.get(key))?.value ?? null,
    write: async (value) => {
      await database.outbox.put({ key, value });
    },
  };
}

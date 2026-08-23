'use client';

import {
  adoptOrphaned,
  createEventOutbox,
  type EnvelopeContext,
  type EventOutbox,
  type FlushSummary,
  type IngestBatch,
  type OrphanedMutation,
  type QueueStatus,
} from '@pharmacy/sync';

import type { SaleCreateRequest } from '@pharmacy/api';

import { database, localRecord } from './database';

const OUTBOX_KEY = 'sale_outbox_v1';

const storage = localRecord(OUTBOX_KEY);

type SalePayload = SaleCreateRequest;

export type SaleOutbox = EventOutbox<SalePayload>;
export type SaleQueueStatus = QueueStatus<SalePayload>;
export type Ingest = IngestBatch<SalePayload>;
export type FlushResult = FlushSummary;

const outbox: SaleOutbox = createEventOutbox<SalePayload>(storage, 'sale.create');

/**
 * Queue a completed cart as an offline sale, answering with the event id it took.
 *
 * `createdAt` is the moment of the sale, not of the upload: a sale rung up on
 * Tuesday must not be dated Thursday because that is when the browser found the
 * server again.
 */
export async function queueSale(payload: SalePayload, context: EnvelopeContext): Promise<string> {
  return (await outbox.queue(payload, context)).envelope.eventId;
}

export async function queueStatus(): Promise<SaleQueueStatus> {
  return outbox.status();
}

/**
 * Put the queue back in a sendable state. Call once when the POS screen mounts.
 *
 * Two jobs: return entries stranded mid-upload by a closed tab (they are
 * `uploading`, which no flush will pick up), and take in anything the previous
 * queue implementation left behind.
 */
export async function recoverOutbox(): Promise<number> {
  await outbox.recover();
  return adoptLegacySales();
}

/** Drop a settled entry once someone has dealt with it. Refuses anything still owed. */
export async function forgetSale(eventId: string): Promise<boolean> {
  return outbox.forget(eventId);
}

/**
 * Upload due sales through `POST /sync/events`.
 *
 * Every queued sale used to be replayed against `/sales` with a client-generated
 * idempotency key instead, which left the entire sync protocol -- device identity,
 * client sequences, per-event acks, the pull feed -- unexercised by the one client
 * that depends on it.
 */
export async function flushQueue(ingest: Ingest, nowUtcIso = new Date().toISOString()): Promise<FlushResult> {
  return outbox.flush(ingest, { nowUtcIso });
}

/**
 * Move rows written by the pre-sync queue into the outbox, as sales needing a person.
 *
 * They cannot be uploaded -- see `adoptOrphaned` -- but leaving them in a table
 * nothing reads any more would be a deletion with extra steps. The rows are
 * cleared only after the adopting write has committed, so an interrupted
 * migration repeats rather than loses.
 */
async function adoptLegacySales(): Promise<number> {
  const rows = await database.queue.toArray();
  if (rows.length === 0) return 0;
  const orphans: OrphanedMutation<SalePayload>[] = rows.map((row) => ({
    eventId: row.id,
    createdAt: row.createdAt,
    payload: row.body,
  }));
  const adopted = await adoptOrphaned(outbox.store, orphans, legacyReason);
  await database.queue.bulkDelete(rows.map((row) => row.id));
  return adopted;
}

const legacyReason =
  'Queued by an older version of this app, which did not record which store or terminal took it. It cannot be uploaded and must be re-entered by hand.';

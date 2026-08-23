import type { SaleCreateRequest } from '@pharmacy/api';
import { createId } from '@pharmacy/core';
import {
  createEventOutbox,
  envelopeContextFor,
  type EnvelopeContext,
  type EventOutbox,
  type FlushSummary,
  type IngestBatch,
  type QueueStatus,
} from '@pharmacy/sync';

import { openMobileDatabase, createSQLiteAdapter } from '../platform/nativeAdapters';
import { hasDueEntries } from './backgroundSync';

/**
 * A fresh idempotency key for an online sale.
 *
 * UUIDv7, the same generator the sync envelopes use, rather than the
 * `Math.random().toString(36).slice(2, 10)` this replaces: that was around forty
 * bits from a non-cryptographic source, on the shell that rings up the most
 * sales, guarding the one value that stops a retried upload from being booked
 * twice. Two phones seeded alike, or one unlucky draw, and a real sale is
 * silently discarded as a duplicate of another.
 */
export function newIdempotencyKey(): string {
  return `mpos-${createId()}`;
}

/**
 * The blob the outbox lives in.
 *
 * Deliberately the same key the previous implementation used, so a phone
 * upgrading with sales already queued keeps them -- the store reads the older
 * bare-array layout and recovers the sequence counter from the entries. The
 * separate `pos_device_context_v1` record it used to pair with is gone: holding
 * the counter apart from the entries it numbers meant two writes where there
 * needed to be one, and a failure between them burned a client sequence.
 */
const OUTBOX_KEY = 'pos_outbox_v1';

type SalePayload = SaleCreateRequest;

export type SaleOutbox = EventOutbox<SalePayload>;
export type SaleQueueStatus = QueueStatus<SalePayload>;
export type Ingest = IngestBatch<SalePayload>;
export type FlushResult = FlushSummary;

let outboxReady: Promise<SaleOutbox> | null = null;

function outbox(): Promise<SaleOutbox> {
  outboxReady ??= openMobileDatabase()
    .then(createSQLiteAdapter)
    .then((adapter) =>
      createEventOutbox<SalePayload>(
        { read: () => adapter.get(OUTBOX_KEY), write: (value) => adapter.set(OUTBOX_KEY, value) },
        'sale.create',
      ),
    );
  return outboxReady;
}

/**
 * The identity every offline envelope is stamped with, or why it cannot be built.
 *
 * Re-exported rather than reimplemented: web and desktop need exactly the same
 * mapping from "who is signed in" to "what an envelope must carry", and three
 * copies of it is how the three shells drifted apart in the first place.
 */
export const envelopeContext = envelopeContextFor;

/**
 * Queue a completed cart as an offline sale, answering with the event id it took.
 *
 * `createdAt` is the moment of the sale, not the moment of the upload: a sale
 * rung up on Tuesday must not be dated Thursday because that is when the phone
 * found signal.
 */
export async function queueSale(payload: SalePayload, context: EnvelopeContext): Promise<string> {
  return (await (await outbox()).queue(payload, context)).envelope.eventId;
}

export async function queueStatus(): Promise<SaleQueueStatus> {
  return (await outbox()).status();
}

export async function pendingCount(): Promise<number> {
  return (await queueStatus()).pending;
}

/**
 * Whether the engine would send anything right now -- the question an automatic
 * trigger asks before spending a request. Answered over the raw snapshot via
 * `dueForUpload`, so a sale waiting on backoff is not due while one that was
 * never tried is; the queue summary's `nextRetryAt` cannot make that distinction.
 */
export async function hasDueUploads(nowUtcIso = new Date().toISOString()): Promise<boolean> {
  const queue = await outbox();
  const { entries } = await queue.store.snapshot();
  return hasDueEntries(entries, nowUtcIso);
}

/** Put entries left mid-upload by a killed app back in line. Call once at startup. */
export async function recoverOutbox(): Promise<void> {
  await (await outbox()).recover();
}

/** Drop a settled entry once someone has dealt with it. Refuses anything still owed. */
export async function forgetSale(eventId: string): Promise<boolean> {
  return (await outbox()).forget(eventId);
}

/**
 * Upload due sales through `POST /sync/events`.
 *
 * This used to post each entry to `/sales` instead, which meant the whole sync
 * protocol -- device identity, client sequences, per-event acks, the pull feed --
 * was never exercised by the one client that needs it. It also flattened every
 * outcome into "worked" or "did not": any non-network error rejected the sale
 * outright, so an `INSUFFICIENT_STOCK` against a batch the shop had not yet
 * booked in threw away a sale that was already paid for.
 */
export async function flushQueue(ingest: Ingest, nowUtcIso = new Date().toISOString()): Promise<FlushResult> {
  const queue = await outbox();
  // Legacy entries first: one malformed envelope is a 422 for the whole batch, so
  // they are taken out of the way before anything is sent.
  const quarantined = await quarantineUnsendable(queue);
  const summary = await queue.flush(ingest, { nowUtcIso });
  if (quarantined === 0) return summary;
  return {
    ...summary,
    rejected: summary.rejected + quarantined,
    firstError:
      summary.firstError ??
      `${quarantined} sale(s) queued by an older version of this app cannot be uploaded and must be re-entered by hand.`,
  };
}

/**
 * Reject entries the ingest endpoint would refuse as malformed.
 *
 * `SyncEventEnvelopeIn` types the identity fields as UUIDs, and an earlier build
 * of this app stamped `deviceId` as `mobile-abcd1234`. One such entry in a batch
 * is a 422 for the whole batch -- including the well-formed sales beside it, which
 * would then look permanently unsendable for a reason no counter could act on. So
 * they are taken out and named.
 */
async function quarantineUnsendable(queue: SaleOutbox): Promise<number> {
  let count = 0;
  await queue.store.mutate((snapshot) => {
    const entries = snapshot.entries.map((entry) => {
      if (entry.status === 'acknowledged' || entry.status === 'rejected') return entry;
      const { deviceId, storeId, organizationId, userId } = entry.envelope;
      if (isUuid(deviceId) && isUuid(storeId) && isUuid(organizationId) && isUuid(userId)) return entry;
      count += 1;
      return {
        ...entry,
        status: 'rejected' as const,
        nextAttemptAt: null,
        error: 'Queued before this phone was registered as a device, so the server cannot accept it. Re-enter this sale by hand.',
      };
    });
    return { ...snapshot, entries };
  });
  return count;
}

function isUuid(value: string): boolean {
  return /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(value);
}

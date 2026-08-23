import type { SaleCreateRequest } from '@pharmacy/api';
import {
  adoptOrphaned,
  createEventOutbox,
  dueForUpload,
  type EnvelopeContext,
  type EventOutbox,
  type FlushSummary,
  type IngestBatch,
  type OrphanedMutation,
  type OutboxStorage,
  type QueueStatus,
} from '@pharmacy/sync';

import { desktopPlatform } from '../platform/runtime';

/**
 * A sale queued by the pre-sync implementation: an idempotency key and a body,
 * destined for `POST /sales`.
 *
 * Read once, then cleared. That queue had no device identity, no client sequence
 * and no per-event acks, so the sync protocol went entirely unused -- and it marked
 * a sale permanently rejected on any non-network error, which discarded paid-for
 * sales over an `INSUFFICIENT_STOCK` that the next delivery would have resolved.
 */
type LegacySale = { id: string; body: SaleCreateRequest; createdAt: string; rejection?: { reason: string; at: string } };

const LEGACY_KEY = 'desktop_sale_queue_v1';
const OUTBOX_KEY = 'desktop_sale_outbox_v1';

/**
 * A new key rather than reusing the old one.
 *
 * The old blob is a bare array of `LegacySale`, and the outbox decoder reads a bare
 * array as its own pre-snapshot layout -- it would take those rows for envelopes
 * and fall over reading fields that are not there. Migrating explicitly is the only
 * honest way across.
 */
const storage: OutboxStorage = {
  read: async () => (await desktopPlatform()).database.get(OUTBOX_KEY),
  write: async (value) => {
    await (await desktopPlatform()).database.set(OUTBOX_KEY, value);
  },
};

type SalePayload = SaleCreateRequest;

export type SaleOutbox = EventOutbox<SalePayload>;
export type SaleQueueStatus = QueueStatus<SalePayload>;
export type Ingest = IngestBatch<SalePayload>;
export type FlushResult = FlushSummary;

const outbox: SaleOutbox = createEventOutbox<SalePayload>(storage, 'sale.create');

/**
 * Store a sale for later upload, answering with the event id it was queued under.
 *
 * Rejects if the write does not reach disk. The caller must not tell the cashier
 * the sale is queued unless it is: "queued" that was only ever in memory reads as
 * safe and is gone at the next restart.
 */
export async function queueSale(payload: SalePayload, context: EnvelopeContext): Promise<string> {
  return (await outbox.queue(payload, context)).envelope.eventId;
}

export async function queueStatus(): Promise<SaleQueueStatus> {
  return outbox.status();
}

/**
 * Put the queue back in a sendable state. Call once when the till screen mounts.
 *
 * Two jobs: return entries stranded mid-upload by a killed app (they sit in
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

/** Upload due sales through `POST /sync/events`, which answers per event. */
export async function flushQueue(ingest: Ingest, nowUtcIso = new Date().toISOString()): Promise<FlushResult> {
  return outbox.flush(ingest, { nowUtcIso });
}

/**
 * Whether the engine would send anything right now: pending entries always, and
 * failed ones once their backoff has elapsed (`dueForUpload`). The automatic
 * flush asks this instead of blind-posting, so a queue sitting out a retry
 * backoff is left alone rather than poked every tick.
 */
export async function uploadsDue(nowUtcIso = new Date().toISOString()): Promise<boolean> {
  const snapshot = await outbox.store.snapshot();
  return dueForUpload(snapshot.entries, nowUtcIso).length > 0;
}

/** Cadence of the background flush timer, between the plan's 20s and 30s bounds. */
export const AUTO_FLUSH_INTERVAL_MS = 25_000;

/** How one enqueued flush should behave. */
export type FlushRequest = {
  nowUtcIso?: string;
  /** Automatic path: skip without posting when the engine has nothing due. */
  onlyIfDue?: boolean;
  /** Cap on entries per batch, forwarded to the engine. */
  batchLimit?: number;
};

let flushChain: Promise<FlushResult | null> = Promise.resolve(null);

/**
 * Run one flush, serializing every caller behind the previous one.
 *
 * `onlyIfDue` is the automatic path: it skips (answering null) when the engine
 * has nothing uploadable right now, instead of posting a batch that would only
 * earn another backoff step. Manual flushes always run. Either way a second
 * flush started mid-flight waits here for the first to settle -- and even if
 * two did overlap, entries claimed as `uploading` are invisible to
 * `dueForUpload`, so no sale could be posted twice.
 *
 * The chain always resumes, failed or not: one unreadable store read must not
 * wedge uploads off for the rest of the session.
 */
export function enqueueFlush(ingest: Ingest, options: FlushRequest = {}): Promise<FlushResult | null> {
  const run = async (): Promise<FlushResult | null> => {
    const nowUtcIso = options.nowUtcIso ?? new Date().toISOString();
    if (options.onlyIfDue === true && !(await uploadsDue(nowUtcIso))) return null;
    const { batchLimit } = options;
    return outbox.flush(ingest, { nowUtcIso, ...(batchLimit === undefined ? {} : { batchLimit }) });
  };
  // `then(run, run)` rather than `then(run)`: the next flush runs whether or
  // not this one settled cleanly, matching the outbox store's own chain.
  const result = flushChain.then(run, run);
  flushChain = result.then(
    () => null,
    () => null,
  );
  return result;
}

/**
 * Move rows written by the pre-sync queue into the outbox, as sales needing a person.
 *
 * They cannot be uploaded -- see `adoptOrphaned` -- but leaving them under a key
 * nothing reads any more would be a deletion with extra steps. The old key is
 * cleared only after the adopting write has committed, so an interrupted migration
 * repeats rather than loses.
 */
async function adoptLegacySales(): Promise<number> {
  const store = (await desktopPlatform()).database;
  const raw = await store.get(LEGACY_KEY);
  if (raw === null) return 0;
  let rows: LegacySale[];
  try {
    rows = JSON.parse(raw) as LegacySale[];
  } catch {
    // Unreadable. Left in place rather than removed: it may still be recoverable
    // by hand, and there is nothing to gain from destroying it here.
    throw new Error('The sale queue left by the previous version of this app is unreadable');
  }
  const orphans: OrphanedMutation<SalePayload>[] = rows
    .filter((row) => typeof row?.id === 'string' && row.body !== undefined)
    .map((row) => ({ eventId: row.id, createdAt: row.createdAt, payload: row.body }));
  const adopted = await adoptOrphaned(outbox.store, orphans, legacyReason);
  await store.remove(LEGACY_KEY);
  return adopted;
}

const legacyReason =
  'Queued by an older version of this app, which did not record which store or till took it. It cannot be uploaded and must be re-entered by hand.';

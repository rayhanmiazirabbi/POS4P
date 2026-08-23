import { createId } from '@pharmacy/core';

export type SyncEnvelope<T = unknown> = { eventId: string; idempotencyKey: string; deviceId: string; organizationId: string; storeId: string; userId: string; eventType: string; createdAt: string; clientSequence: number; payload: T };
export type OutboxStatus = 'pending' | 'uploading' | 'acknowledged' | 'failed' | 'rejected';
export type OutboxEntry<T = unknown> = { envelope: SyncEnvelope<T>; status: OutboxStatus; attempts: number; nextAttemptAt: string | null; error: string | null };
export type SyncAcknowledgement = { eventId: string; serverSequence: number; duplicate: boolean };
export type RemoteChange<T = unknown> = { serverSequence: number; eventType: string; payload: T };
/** One page of `GET /sync/events`. `nextCursor` is a server sequence, matching `PullResponse` in `backend/app/schemas/sync.py` -- it was typed `string` here, which no caller could have fed back to the endpoint. */
export type PullPage<T = unknown> = { changes: readonly RemoteChange<T>[]; nextCursor: number; hasMore: boolean };

export type ConnectivityState = 'online' | 'offline';
export type DownloadCursor = { lastServerSequence: number };

export type RetryBackoff = { baseMs: number; maxMs: number; jitterRatio: number };
export const defaultBackoff: RetryBackoff = { baseMs: 1000, maxMs: 60_000, jitterRatio: 0 };

const requiredTextFields = ['eventId', 'idempotencyKey', 'deviceId', 'organizationId', 'storeId', 'userId', 'eventType'] as const;

function isIsoUtc(value: string): boolean {
  return /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?Z$/.test(value) && !Number.isNaN(Date.parse(value));
}

export function validateEnvelope(envelope: SyncEnvelope): readonly string[] {
  const problems: string[] = [];
  for (const field of requiredTextFields) {
    if (envelope[field].trim() === '') problems.push(`${field} is required`);
  }
  if (!isIsoUtc(envelope.createdAt)) problems.push('createdAt must be a UTC ISO timestamp');
  if (!Number.isInteger(envelope.clientSequence) || envelope.clientSequence < 1) problems.push('clientSequence must be a positive integer');
  return problems;
}

/**
 * Build an envelope, defaulting the identity fields.
 *
 * The idempotency key defaults to the event id, which is a fresh uuidv7 per
 * created envelope. It used to be `${deviceId}:${clientSequence}` -- a
 * *positional* key, meaning "the Nth operation from this device", and two
 * ordinary situations make two different sales share one position:
 *
 *   - One cashier on two phones. `deviceId` was derived from the signed-in user,
 *     so both phones answered to the same id and each kept its own counter.
 *     Both first offline sales were `mobile-abcd1234:1`.
 *   - A reinstall, or storage cleared. The counter restarts at 1 while the server
 *     still remembers the keys it has already seen.
 *
 * The failure is silent and total: `POST /sales` replays the stored response, so
 * the second phone is told **201 Created and handed the first sale's receipt
 * number**. Stock is never decremented, the money is never recorded, and every
 * screen says the sale went through. The event id cannot collide this way, and
 * the server derives its own key from it (`offline:{event_id}`) regardless.
 *
 * An explicit `idempotencyKey` is still honoured for callers that have a genuine
 * business key to deduplicate on.
 */
export function createSyncEnvelope<T>(input: Omit<SyncEnvelope<T>, 'eventId' | 'idempotencyKey' | 'createdAt'> & Partial<Pick<SyncEnvelope<T>, 'eventId' | 'idempotencyKey' | 'createdAt'>>): SyncEnvelope<T> {
  const eventId = input.eventId ?? createId();
  const envelope: SyncEnvelope<T> = {
    ...input,
    eventId,
    idempotencyKey: input.idempotencyKey ?? eventId,
    createdAt: input.createdAt ?? new Date().toISOString(),
  };
  const problems = validateEnvelope(envelope);
  if (problems.length > 0) throw new Error(`Invalid sync envelope: ${problems.join('; ')}`);
  return envelope;
}

export function enqueue<T>(outbox: readonly OutboxEntry[], envelope: SyncEnvelope<T>): OutboxEntry[] { if (outbox.some((entry) => entry.envelope.eventId === envelope.eventId || entry.envelope.idempotencyKey === envelope.idempotencyKey)) return [...outbox]; if (!Number.isInteger(envelope.clientSequence) || envelope.clientSequence < 1) throw new Error('Invalid client sequence'); return [...outbox, { envelope, status: 'pending', attempts: 0, nextAttemptAt: null, error: null }]; }
export function acknowledge(outbox: readonly OutboxEntry[], acknowledgement: SyncAcknowledgement): OutboxEntry[] { return outbox.map((entry) => entry.envelope.eventId === acknowledgement.eventId ? { ...entry, status: 'acknowledged', error: null, nextAttemptAt: null } : entry); }
export function markFailed(outbox: readonly OutboxEntry[], eventId: string, reason: string, nextAttemptAt: string): OutboxEntry[] { return outbox.map((entry) => entry.envelope.eventId === eventId ? { ...entry, status: 'failed', attempts: entry.attempts + 1, error: reason, nextAttemptAt } : entry); }

export function addMs(isoTimestamp: string, ms: number): string {
  if (!isIsoUtc(isoTimestamp)) throw new Error('Timestamp must be a UTC ISO string');
  if (!Number.isFinite(ms)) throw new Error('Offset must be a finite number');
  return new Date(Date.parse(isoTimestamp) + ms).toISOString();
}

/**
 * Exponential backoff with optional jitter and a hard cap.
 *
 * `attempts` is a count, not an index: the first retry is `attempts === 1` and
 * waits `baseMs`. A `retryDelayMs(attempt)` sat here too, computing the identical
 * curve one step out of phase (`retryDelayMs(n) === computeBackoffMs(n + 1)`),
 * with no caller but its own test. Two same-shaped helpers disagreeing about
 * whether the argument counts from zero is how a retry storm gets written, and
 * `@pharmacy/api` exports its own unrelated `retryDelayMs`, so importing both
 * into one file collided on the name with silently different meanings.
 */
export function computeBackoffMs(attempts: number, backoff: RetryBackoff = defaultBackoff, random: () => number = Math.random): number {
  if (!Number.isInteger(attempts) || attempts < 1) throw new Error('Attempts must be a positive integer');
  const exponential = Math.min(backoff.maxMs, backoff.baseMs * 2 ** (attempts - 1));
  if (backoff.jitterRatio <= 0) return exponential;
  const spread = exponential * Math.min(1, backoff.jitterRatio);
  return Math.max(0, Math.min(backoff.maxMs, Math.round(exponential - spread + random() * spread * 2)));
}

export type UploadClaim = { outbox: OutboxEntry[]; entry: OutboxEntry | null };

/** Only pending/failed entries may be claimed; an in-flight event cannot be uploaded twice concurrently. */
export function claimForUpload(outbox: readonly OutboxEntry[], eventId: string): UploadClaim {
  const target = outbox.find((entry) => entry.envelope.eventId === eventId);
  if (!target || (target.status !== 'pending' && target.status !== 'failed')) return { outbox: [...outbox], entry: null };
  return {
    outbox: outbox.map((entry) => (entry.envelope.eventId === eventId ? { ...entry, status: 'uploading' as const } : entry)),
    entry: { ...target, status: 'uploading' },
  };
}

/** Crash during upload: the event goes back to pending with its failure history intact. */
export function releaseUpload(outbox: readonly OutboxEntry[], eventId: string, reason: string | null = null): OutboxEntry[] {
  return outbox.map((entry) => entry.envelope.eventId === eventId && entry.status === 'uploading' ? { ...entry, status: 'pending' as const, error: reason } : entry);
}

export function recoverAfterRestart(outbox: readonly OutboxEntry[]): OutboxEntry[] {
  return outbox.map((entry) => entry.status === 'uploading' ? { ...entry, status: 'pending' as const } : entry);
}

export type RetryOutcome = { outbox: OutboxEntry[]; retried: boolean };

export function scheduleRetry(outbox: readonly OutboxEntry[], eventId: string, reason: string, nowUtcIso: string, backoff: RetryBackoff = defaultBackoff, random: () => number = Math.random): RetryOutcome {
  const target = outbox.find((entry) => entry.envelope.eventId === eventId);
  if (!target || target.status === 'acknowledged' || target.status === 'rejected') return { outbox: [...outbox], retried: false };
  const attempts = target.attempts + 1;
  const nextAttemptAt = addMs(nowUtcIso, computeBackoffMs(attempts, backoff, random));
  return {
    outbox: outbox.map((entry) => entry.envelope.eventId === eventId ? { ...entry, status: 'failed' as const, attempts, error: reason, nextAttemptAt } : entry),
    retried: true,
  };
}

/** Permanent rejection (revoked device, irrecoverable validation conflict): never scheduled again; re-rejecting is a no-op so the first actionable reason survives. */
export function rejectEvent(outbox: readonly OutboxEntry[], eventId: string, reason: string): OutboxEntry[] {
  return outbox.map((entry) => entry.envelope.eventId === eventId && entry.status !== 'acknowledged' && entry.status !== 'rejected' ? { ...entry, status: 'rejected' as const, error: reason, nextAttemptAt: null } : entry);
}

/** Uploadable right now: pending always, failed once its backoff has elapsed; ordered by client sequence. */
export function dueForUpload(outbox: readonly OutboxEntry[], nowUtcIso: string): OutboxEntry[] {
  const now = Date.parse(nowUtcIso);
  return outbox
    .filter((entry) => {
      if (entry.status === 'pending') return true;
      if (entry.status === 'failed') return entry.nextAttemptAt !== null && Date.parse(entry.nextAttemptAt) <= now;
      return false;
    })
    .sort((a, b) => a.envelope.clientSequence - b.envelope.clientSequence || a.envelope.eventId.localeCompare(b.envelope.eventId));
}

export function initialCursor(): DownloadCursor { return { lastServerSequence: 0 }; }

/** Monotonic by design: a late page of older events never moves the cursor backwards. */
export function advanceCursor(cursor: DownloadCursor, changes: readonly RemoteChange[]): DownloadCursor {
  const highest = changes.reduce((max, change) => (change.serverSequence > max ? change.serverSequence : max), cursor.lastServerSequence);
  return highest === cursor.lastServerSequence ? cursor : { lastServerSequence: highest };
}

export type PartitionedChanges<T> = { fresh: RemoteChange<T>[]; duplicates: RemoteChange<T>[] };

export function partitionRemoteChanges<T>(cursor: DownloadCursor, changes: readonly RemoteChange<T>[]): PartitionedChanges<T> {
  const fresh: RemoteChange<T>[] = [];
  const duplicates: RemoteChange<T>[] = [];
  for (const change of changes) (change.serverSequence > cursor.lastServerSequence ? fresh : duplicates).push(change);
  return { fresh, duplicates };
}

/** Missing server sequences inside one page; the cursor still tracks the highest applied event. */
export function findSequenceGaps(changes: readonly RemoteChange[]): readonly number[] {
  const sequences = [...new Set(changes.map((change) => change.serverSequence))].sort((a, b) => a - b);
  const gaps: number[] = [];
  for (let index = 1; index < sequences.length; index += 1) {
    const previous = sequences[index - 1];
    const current = sequences[index];
    if (previous === undefined || current === undefined) continue;
    for (let missing = previous + 1; missing < current; missing += 1) gaps.push(missing);
  }
  return gaps;
}

export type SyncSummary = {
  connectivity: ConnectivityState;
  total: number;
  pending: number;
  uploading: number;
  failed: number;
  rejected: number;
  acknowledged: number;
  nextRetryAt: string | null;
  cursor: number;
  idle: boolean;
};

export function summarize(outbox: readonly OutboxEntry[], cursor: DownloadCursor, connectivity: ConnectivityState): SyncSummary {
  const counts = { pending: 0, uploading: 0, failed: 0, rejected: 0, acknowledged: 0 };
  let nextRetryAt: string | null = null;
  for (const entry of outbox) {
    counts[entry.status] += 1;
    if (entry.status === 'failed' && entry.nextAttemptAt !== null && (nextRetryAt === null || Date.parse(entry.nextAttemptAt) < Date.parse(nextRetryAt))) nextRetryAt = entry.nextAttemptAt;
  }
  const outstanding = counts.pending + counts.uploading + counts.failed;
  return {
    connectivity,
    total: outbox.length,
    ...counts,
    nextRetryAt,
    cursor: cursor.lastServerSequence,
    idle: connectivity === 'online' && outstanding === 0,
  };
}

export function sortRemoteChanges<T>(changes: readonly RemoteChange<T>[]): RemoteChange<T>[] { return [...changes].sort((a, b) => a.serverSequence - b.serverSequence); }

export {
  adoptOrphaned,
  applyAcks,
  createEventOutbox,
  createOutboxStore,
  describeIngestFailure,
  emptySnapshot,
  envelopeContextFor,
  envelopeFactory,
  flushOutbox,
  isPermanentIngestCode,
  summarizeQueue,
  toWireEnvelope,
  type AckOutcome,
  type EnvelopeContext,
  type EventOutbox,
  type FlushOptions,
  type FlushSummary,
  type IngestAck,
  type IngestBatch,
  type OrphanedMutation,
  type OutboxSnapshot,
  type OutboxStorage,
  type OutboxStore,
  type QueueStatus,
  type SignedInIdentity,
  type StuckEntry,
  type WireEnvelope,
} from './outbox';

export {
  createShelfStore,
  describeShelfAge,
  loadShelf,
  matchShelf,
  readShelf,
  scanShelf,
  submitShelfEntry,
  toShelfProduct,
  type ShelfCache,
  type ShelfLoad,
  type ShelfMatch,
  type ShelfMatchKind,
  type ShelfProduct,
  type ShelfRead,
  type ShelfScan,
  type ShelfSource,
  type ShelfStore,
} from './shelf';

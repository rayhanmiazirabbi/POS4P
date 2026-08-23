import {
  acknowledge,
  claimForUpload,
  createSyncEnvelope,
  dueForUpload,
  enqueue,
  recoverAfterRestart,
  rejectEvent,
  releaseUpload,
  scheduleRetry,
  type OutboxEntry,
  type RetryBackoff,
  type SyncEnvelope,
  defaultBackoff,
} from './index';

/**
 * One offline mutation's outcome, as the server reports it.
 *
 * Mirrors `SyncAck` in `backend/app/schemas/sync.py`. `errorCode` is the
 * discriminator: absent means applied, present means this event did not apply and
 * only this event -- the rest of the batch stands.
 */
export type IngestAck = {
  eventId: string;
  serverSequence?: number | null;
  duplicate?: boolean;
  errorCode?: string | null;
};

/**
 * Ingest error codes that will never succeed on a retry, from
 * `backend/app/services/sync.py`.
 *
 * Everything *not* listed here is treated as retryable, which is the deliberate
 * direction to fail in: a wrongly-retried event is deduplicated by the server on
 * its event id and costs one wasted request, while a wrongly-rejected event is a
 * sale that was rung up, taken payment for, and then dropped.
 *
 * `INSUFFICIENT_STOCK` and `CONFLICT` are absent on purpose. They read like
 * verdicts but they are statements about data that can change: the shop receives
 * the batch the offline sale drew down, and the event then applies unchanged.
 * Rejecting them -- which is what the client used to do with every non-network
 * error -- discarded a real sale over a stock count that a delivery would fix.
 */
const permanentIngestCodes: readonly string[] = [
  'IDENTITY_MISMATCH',
  'UNSUPPORTED_EVENT_TYPE',
  'VALIDATION_ERROR',
  'OUT_OF_ORDER',
];

export function isPermanentIngestCode(code: string): boolean {
  return permanentIngestCodes.includes(code);
}

/**
 * Why a permanently-rejected event needs a person, in words a counter can act on.
 *
 * `OUT_OF_ORDER` in particular is not a lost cause but a lost *slot*: the store's
 * checkpoint has already passed this client sequence, so the server will refuse
 * this envelope no matter how often it is sent, while the sale itself is real and
 * unrecorded. Saying "rejected" and moving on would quietly lose it.
 */
export function describeIngestFailure(code: string): string {
  switch (code) {
    case 'OUT_OF_ORDER':
      return 'The server has already moved past this sale\'s position in the queue. It cannot upload as-is and must be re-entered by hand.';
    case 'IDENTITY_MISMATCH':
      return 'This sale was queued against a different store or device. Sign in on the right store and re-enter it.';
    case 'UNSUPPORTED_EVENT_TYPE':
      return 'The server does not accept this kind of change. The app needs updating.';
    case 'VALIDATION_ERROR':
      return 'The server refused the sale as malformed. It must be re-entered by hand.';
    default:
      return code;
  }
}

export type AckOutcome<T = unknown> = {
  outbox: OutboxEntry<T>[];
  applied: readonly string[];
  duplicates: readonly string[];
  retrying: readonly string[];
  rejected: readonly string[];
  released: readonly string[];
};

/**
 * Fold a batch of acks into the outbox, one event at a time.
 *
 * Events that were uploaded but have no ack are *released*, not left as they
 * were. An entry stuck in `uploading` is invisible to `dueForUpload`, so a
 * truncated or partial response would park a real sale in a state nothing ever
 * retries -- recoverable only by restarting the app.
 */
export function applyAcks<T>(
  outbox: readonly OutboxEntry<T>[],
  acks: readonly IngestAck[],
  uploadedEventIds: readonly string[],
  nowUtcIso: string,
  backoff: RetryBackoff = defaultBackoff,
  random: () => number = Math.random,
): AckOutcome<T> {
  let next = [...outbox] as OutboxEntry<T>[];
  const applied: string[] = [];
  const duplicates: string[] = [];
  const retrying: string[] = [];
  const rejected: string[] = [];
  const released: string[] = [];

  for (const ack of acks) {
    const code = ack.errorCode ?? null;
    if (code === null) {
      next = acknowledge(next, {
        eventId: ack.eventId,
        serverSequence: ack.serverSequence ?? 0,
        duplicate: ack.duplicate === true,
      }) as OutboxEntry<T>[];
      (ack.duplicate === true ? duplicates : applied).push(ack.eventId);
      continue;
    }
    if (isPermanentIngestCode(code)) {
      next = rejectEvent(next, ack.eventId, describeIngestFailure(code)) as OutboxEntry<T>[];
      rejected.push(ack.eventId);
      continue;
    }
    next = scheduleRetry(next, ack.eventId, code, nowUtcIso, backoff, random).outbox as OutboxEntry<T>[];
    retrying.push(ack.eventId);
  }

  const answered = new Set(acks.map((ack) => ack.eventId));
  for (const eventId of uploadedEventIds) {
    if (answered.has(eventId)) continue;
    next = releaseUpload(next, eventId, 'The server did not answer for this sale; it will be retried.') as OutboxEntry<T>[];
    released.push(eventId);
  }

  return { outbox: next, applied, duplicates, retrying, rejected, released };
}

/** Persisted outbox state. The client sequence counter lives here so allocating one and queueing the event it belongs to is a single write. */
export type OutboxSnapshot<T = unknown> = {
  entries: readonly OutboxEntry<T>[];
  lastClientSequence: number;
};

/** The one blob of durable storage an outbox needs. SQLite, localStorage, and the Tauri store all satisfy it. */
export type OutboxStorage = {
  read(): Promise<string | null>;
  write(value: string): Promise<void>;
};

export const emptySnapshot: OutboxSnapshot<never> = Object.freeze({ entries: Object.freeze([]), lastClientSequence: 0 });

function decode<T>(raw: string | null): OutboxSnapshot<T> {
  if (raw === null) return { entries: [], lastClientSequence: 0 };
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    // Unreadable blob. Returning an empty outbox here would silently discard
    // every queued sale, so the caller is told instead.
    throw new Error('The offline sale queue on this device is unreadable');
  }
  if (Array.isArray(parsed)) {
    // Pre-snapshot layout: a bare array of entries with the counter kept
    // elsewhere. Read it rather than dropping it, and recover the counter from
    // the entries themselves so the next sale cannot reuse a sequence.
    const entries = parsed as OutboxEntry<T>[];
    return { entries, lastClientSequence: highestSequence(entries) };
  }
  if (typeof parsed !== 'object' || parsed === null || !Array.isArray((parsed as OutboxSnapshot<T>).entries)) {
    throw new Error('The offline sale queue on this device is unreadable');
  }
  const snapshot = parsed as OutboxSnapshot<T>;
  return {
    entries: snapshot.entries,
    // Never trust a stored counter that is behind its own entries: reusing a
    // client sequence gets the event refused as OUT_OF_ORDER, permanently.
    lastClientSequence: Math.max(snapshot.lastClientSequence ?? 0, highestSequence(snapshot.entries)),
  };
}

function highestSequence<T>(entries: readonly OutboxEntry<T>[]): number {
  return entries.reduce((max, entry) => Math.max(max, entry.envelope.clientSequence), 0);
}

export type OutboxStore<T = unknown> = {
  snapshot(): Promise<OutboxSnapshot<T>>;
  /** Read, transform, write -- with no other mutation interleaved. */
  mutate(change: (snapshot: OutboxSnapshot<T>) => OutboxSnapshot<T> | Promise<OutboxSnapshot<T>>): Promise<OutboxSnapshot<T>>;
  /** Allocate the next client sequence and queue the envelope built from it, as one write. */
  queue(build: (clientSequence: number) => SyncEnvelope<T>): Promise<OutboxEntry<T>>;
};

/**
 * A durable outbox whose mutations cannot interleave.
 *
 * Every caller here does read -> transform -> write against a single blob, and
 * without a lock two overlapping mutations both read the same state and the
 * second write erases the first. That is not a theoretical race: completing a
 * sale and the reconnect flush run concurrently by design, and the cost of
 * losing the write is losing a sale that was already paid for. So mutations are
 * serialized through one promise chain.
 *
 * A failed mutation must not wedge the chain either -- the queue would stop
 * accepting sales for the rest of the session -- so the tail is always resumed.
 */
export function createOutboxStore<T>(storage: OutboxStorage): OutboxStore<T> {
  let tail: Promise<unknown> = Promise.resolve();

  function serialize<R>(operation: () => Promise<R>): Promise<R> {
    // `then(op, op)` rather than `then(op)`: the next mutation runs whether or
    // not the previous one settled cleanly.
    const result = tail.then(operation, operation);
    tail = result.then(
      () => undefined,
      () => undefined,
    );
    return result;
  }

  async function load(): Promise<OutboxSnapshot<T>> {
    return decode<T>(await storage.read());
  }

  async function commit(snapshot: OutboxSnapshot<T>): Promise<OutboxSnapshot<T>> {
    await storage.write(JSON.stringify(snapshot));
    return snapshot;
  }

  return {
    snapshot: () => serialize(load),
    mutate: (change) =>
      serialize(async () => {
        const current = await load();
        return commit(await change(current));
      }),
    queue: (build) =>
      serialize(async () => {
        const current = await load();
        const clientSequence = current.lastClientSequence + 1;
        const envelope = build(clientSequence);
        const entries = enqueue(current.entries as readonly OutboxEntry[], envelope) as OutboxEntry<T>[];
        // The counter advances only alongside the entry it numbered. Saving it
        // first, as the previous implementation did, burned a sequence whenever
        // the enqueue then failed.
        await commit({ entries, lastClientSequence: clientSequence });
        const queued = entries.find((entry) => entry.envelope.eventId === envelope.eventId);
        if (queued === undefined) throw new Error('Sale was not queued');
        return queued;
      }),
  };
}

/** Envelope factory bound to one signed-in device, so callers cannot forget an identity field. */
export type EnvelopeContext = {
  deviceId: string;
  organizationId: string;
  storeId: string;
  userId: string;
};

/**
 * The signed-in identity an envelope is stamped from, as `GET /auth/me` reports it.
 *
 * Structural rather than an import of `CurrentUser`, because this package does not
 * depend on `@pharmacy/api` -- and does not need to: what matters is that the ids
 * come from the server's own rows.
 */
export type SignedInIdentity = {
  organizationId: string;
  storeId?: string | null;
  deviceId?: string | null;
  user: { id: string };
};

/**
 * The identity every offline envelope needs, or null if this session cannot supply it.
 *
 * `deviceId` is the server's device row id, never something the client derives.
 * The mobile shell used to compute `mobile-${userId.slice(0, 8)}`, which inverted
 * the relationship in both directions at once: one cashier's two phones shared an
 * identity and so collided on every client sequence, while the same phone changed
 * identity at each shift change.
 *
 * It is null until a login has bound a device. A sale queued without one can never
 * be uploaded -- `/sync/events` answers `DEVICE_CONTEXT_REQUIRED` -- so callers are
 * expected to refuse the sale up front rather than accept it and strand it.
 */
export function envelopeContextFor(identity: SignedInIdentity): EnvelopeContext | null {
  const storeId = identity.storeId ?? '';
  const deviceId = identity.deviceId ?? '';
  if (storeId === '' || deviceId === '') return null;
  return { deviceId, storeId, organizationId: identity.organizationId, userId: identity.user.id };
}

export function envelopeFactory<T>(context: EnvelopeContext, eventType: string) {
  return (payload: T, clientSequence: number, createdAt?: string): SyncEnvelope<T> =>
    createSyncEnvelope<T>({
      ...context,
      eventType,
      clientSequence,
      payload,
      ...(createdAt === undefined ? {} : { createdAt }),
    });
}

/** An envelope as `POST /sync/events` accepts it: the identity fields, and nothing local. */
export type WireEnvelope<T = unknown> = {
  eventId: string;
  eventType: string;
  clientSequence: number;
  payload: T;
  createdAt: string;
  deviceId: string;
  organizationId: string;
  storeId: string;
  userId: string;
};

/**
 * Project a stored envelope onto the ingest wire format.
 *
 * Field-by-field rather than a spread, because `SyncEventEnvelopeIn` forbids
 * unknown keys: sending `idempotencyKey` -- which is local bookkeeping, and which
 * the server does not want because it derives its own key from the event id --
 * fails the whole batch with a 422 that names a field the counter has never heard
 * of. Every queued sale then looks permanently unsendable.
 */
export function toWireEnvelope<T>(envelope: SyncEnvelope<T>): WireEnvelope<T> {
  return {
    eventId: envelope.eventId,
    eventType: envelope.eventType,
    clientSequence: envelope.clientSequence,
    payload: envelope.payload,
    createdAt: envelope.createdAt,
    deviceId: envelope.deviceId,
    organizationId: envelope.organizationId,
    storeId: envelope.storeId,
    userId: envelope.userId,
  };
}

/** Uploads one batch of envelopes and returns the server's per-event acks. */
export type IngestBatch<T> = (events: readonly WireEnvelope<T>[]) => Promise<readonly IngestAck[]>;

export type FlushSummary = {
  /** Newly applied by the server. */
  uploaded: number;
  /** Already applied on an earlier attempt; equally done, but not new revenue. */
  duplicates: number;
  retrying: number;
  rejected: number;
  /** Still owed to the server after this flush. */
  remaining: number;
  /** The last failure looked like lost connectivity rather than a refusal. */
  offline: boolean;
  firstError: string | null;
};

export type FlushOptions = {
  nowUtcIso?: string;
  batchLimit?: number;
  backoff?: RetryBackoff;
  random?: () => number;
};

/**
 * Upload everything currently due, in batches, and fold each ack back into the queue.
 *
 * Entries are claimed (`uploading`) inside the same serialized write that reads
 * them, so a sale completed mid-flush is not swept into a second batch. Each
 * event id is attempted at most once per flush: without that, an entry the server
 * asked us to retry would be immediately re-sent in the next batch of the same
 * loop, and a permanent rejection would spin forever.
 *
 * A batch that fails to reach the server does not abandon the rest -- but it does
 * stop the loop, because the next batch would fail the same way and each attempt
 * costs the entry another backoff step.
 */
export async function flushOutbox<T>(
  store: OutboxStore<T>,
  ingest: IngestBatch<T>,
  options: FlushOptions = {},
): Promise<FlushSummary> {
  const nowUtcIso = options.nowUtcIso ?? new Date().toISOString();
  const batchLimit = options.batchLimit ?? 25;
  const { backoff = defaultBackoff, random = Math.random } = options;
  const summary: FlushSummary = {
    uploaded: 0, duplicates: 0, retrying: 0, rejected: 0, remaining: 0, offline: false, firstError: null,
  };
  const attempted = new Set<string>();

  for (;;) {
    let claimed: OutboxEntry<T>[] = [];
    await store.mutate((snapshot) => {
      const due = dueForUpload(snapshot.entries, nowUtcIso)
        .filter((entry) => !attempted.has(entry.envelope.eventId))
        .slice(0, batchLimit);
      let entries = snapshot.entries as OutboxEntry<T>[];
      const taken: OutboxEntry<T>[] = [];
      for (const entry of due) {
        const claim = claimForUpload(entries, entry.envelope.eventId);
        entries = claim.outbox as OutboxEntry<T>[];
        if (claim.entry !== null) taken.push(claim.entry as OutboxEntry<T>);
      }
      claimed = taken;
      return { ...snapshot, entries };
    });
    if (claimed.length === 0) break;

    const eventIds = claimed.map((entry) => entry.envelope.eventId);
    for (const eventId of eventIds) attempted.add(eventId);

    let acks: readonly IngestAck[];
    try {
      acks = await ingest(claimed.map((entry) => toWireEnvelope(entry.envelope)));
    } catch (cause) {
      const message = cause instanceof Error ? cause.message : 'Upload failed';
      // Whether the batch reached the server is unknowable from here, and it does
      // not matter: ingest deduplicates on the event id, so each claimed entry
      // simply goes back in line behind a backoff.
      await store.mutate((snapshot) => {
        let entries = snapshot.entries as OutboxEntry<T>[];
        for (const eventId of eventIds) {
          entries = scheduleRetry(entries, eventId, message, nowUtcIso, backoff, random).outbox as OutboxEntry<T>[];
        }
        return { ...snapshot, entries };
      });
      summary.retrying += eventIds.length;
      summary.offline = (cause as { code?: string }).code === 'NETWORK_ERROR';
      summary.firstError ??= message;
      break;
    }

    let folded: AckOutcome<T> | null = null;
    await store.mutate((snapshot) => {
      const outcome = applyAcks<T>(snapshot.entries, acks, eventIds, nowUtcIso, backoff, random);
      folded = outcome;
      return { ...snapshot, entries: outcome.outbox };
    });
    if (folded === null) break; // unreachable: `mutate` awaits the change it is handed
    const outcome: AckOutcome<T> = folded;
    summary.uploaded += outcome.applied.length;
    summary.duplicates += outcome.duplicates.length;
    summary.retrying += outcome.retrying.length + outcome.released.length;
    summary.rejected += outcome.rejected.length;
  }

  const { entries } = await store.snapshot();
  summary.remaining = entries.filter((entry) => entry.status !== 'acknowledged' && entry.status !== 'rejected').length;
  if (summary.firstError === null && (summary.rejected > 0 || summary.retrying > 0)) {
    summary.firstError =
      entries.find((entry) => entry.status === 'rejected' && attempted.has(entry.envelope.eventId))?.error ??
      entries.find((entry) => entry.status === 'failed' && attempted.has(entry.envelope.eventId))?.error ??
      'Some sales were not accepted.';
  }
  return summary;
}

/** A payload left behind by an earlier queue implementation, with no envelope of its own. */
export type OrphanedMutation<T> = { eventId: string; createdAt: string; payload: T };

/**
 * Adopt payloads orphaned by a previous queue implementation as entries a person must settle.
 *
 * They cannot be uploaded. `/sync/events` needs the device, store and user the
 * mutation was made under, and a queue that predates envelopes never recorded
 * them -- stamping them with whoever is signed in now would book last week's sale
 * against whatever store this terminal happens to be pointed at today. So they are
 * adopted as rejected: the payload and its original timestamp survive, they appear
 * in the stuck list, and settling them is a decision a person makes.
 *
 * Adopting beats leaving them where they were. Every one is money taken at the
 * counter for stock that left the shelf, and the abandoned row is the only record
 * of it; a table nothing reads any more is the same as a deletion, just quieter.
 *
 * Idempotent on `eventId`, so a migration interrupted halfway can simply run again.
 */
export async function adoptOrphaned<T>(
  store: OutboxStore<T>,
  orphans: readonly OrphanedMutation<T>[],
  reason: string,
): Promise<number> {
  if (orphans.length === 0) return 0;
  let adopted = 0;
  await store.mutate((snapshot) => {
    const known = new Set(snapshot.entries.map((entry) => entry.envelope.eventId));
    const fresh = orphans
      .filter((orphan) => !known.has(orphan.eventId))
      .map<OutboxEntry<T>>((orphan) => ({
        envelope: {
          eventId: orphan.eventId,
          idempotencyKey: orphan.eventId,
          // No identity, and none can be invented -- see above. The entry is
          // `rejected`, so it is never a candidate for upload and these fields are
          // never read by anything that would send them.
          deviceId: '',
          organizationId: '',
          storeId: '',
          userId: '',
          eventType: 'sale.create',
          createdAt: orphan.createdAt,
          // It never had a client sequence: the queue it came from did not number
          // its entries. Zero keeps it out of the high-water mark this device has
          // actually sent, which the next real sale must not fall behind.
          clientSequence: 0,
          payload: orphan.payload,
        },
        status: 'rejected',
        attempts: 0,
        nextAttemptAt: null,
        error: reason,
      }));
    adopted = fresh.length;
    return { ...snapshot, entries: [...snapshot.entries, ...fresh] };
  });
  return adopted;
}

export type StuckEntry<T> = {
  eventId: string;
  createdAt: string;
  payload: T;
  reason: string | null;
};

export type QueueStatus<T> = {
  /** Still owed to the server. */
  pending: number;
  /** Waiting on a backoff rather than on connectivity. */
  retrying: number;
  /** Permanently refused, and therefore a person's problem. */
  stuck: readonly StuckEntry<T>[];
  /** The earliest scheduled retry, so a screen can say when rather than just "later". */
  nextRetryAt: string | null;
};

export function summarizeQueue<T>(entries: readonly OutboxEntry<T>[]): QueueStatus<T> {
  let pending = 0;
  let retrying = 0;
  let nextRetryAt: string | null = null;
  const stuck: StuckEntry<T>[] = [];
  for (const entry of entries) {
    if (entry.status === 'acknowledged') continue;
    if (entry.status === 'rejected') {
      stuck.push({
        eventId: entry.envelope.eventId,
        createdAt: entry.envelope.createdAt,
        payload: entry.envelope.payload,
        reason: entry.error,
      });
      continue;
    }
    pending += 1;
    if (entry.status === 'failed') {
      retrying += 1;
      if (entry.nextAttemptAt !== null && (nextRetryAt === null || Date.parse(entry.nextAttemptAt) < Date.parse(nextRetryAt))) {
        nextRetryAt = entry.nextAttemptAt;
      }
    }
  }
  return { pending, retrying, stuck, nextRetryAt };
}

/**
 * Everything a POS shell needs from its offline queue, over one storage blob.
 *
 * This exists because all three shells had grown their own queue, and the three
 * had drifted into three different answers to the same question. Two of them
 * marked a sale permanently rejected on *any* non-network error, so an
 * `INSUFFICIENT_STOCK` against a batch the shop had not yet booked in discarded a
 * sale that had already been paid for; none of them used the sync protocol, so
 * device identity, client sequences and per-event acks went unexercised by the
 * only clients that need them. One implementation, one failure model.
 */
export type EventOutbox<T> = {
  /** Queue a mutation. The client sequence is allocated in the same write. */
  queue(payload: T, context: EnvelopeContext, createdAt?: string): Promise<OutboxEntry<T>>;
  status(): Promise<QueueStatus<T>>;
  flush(ingest: IngestBatch<T>, options?: FlushOptions): Promise<FlushSummary>;
  /** Return entries stranded mid-upload by a killed app to the queue. Call at startup. */
  recover(): Promise<void>;
  /** Forget one settled entry. Refuses anything still owed to the server. */
  forget(eventId: string): Promise<boolean>;
  /** Drop everything the server has accepted. The sequence counter is untouched. */
  purgeAcknowledged(): Promise<number>;
  /** The underlying store, for callers that need a bespoke mutation. */
  store: OutboxStore<T>;
};

export function createEventOutbox<T>(storage: OutboxStorage, eventType: string): EventOutbox<T> {
  const store = createOutboxStore<T>(storage);
  return {
    store,
    queue: (payload, context, createdAt) => {
      const build = envelopeFactory<T>(context, eventType);
      return store.queue((clientSequence) => build(payload, clientSequence, createdAt));
    },
    status: async () => summarizeQueue((await store.snapshot()).entries),
    flush: (ingest, options) => flushOutbox(store, ingest, options),
    recover: async () => {
      await store.mutate((snapshot) => ({ ...snapshot, entries: recoverAfterRestart(snapshot.entries) as OutboxEntry<T>[] }));
    },
    forget: async (eventId) => {
      let removed = false;
      await store.mutate((snapshot) => {
        const entries = snapshot.entries.filter((entry) => {
          // A pending or retrying entry is a sale the server has not taken yet.
          // Deleting it on a stray click would destroy the only copy.
          if (entry.envelope.eventId !== eventId) return true;
          if (entry.status !== 'rejected' && entry.status !== 'acknowledged') return true;
          removed = true;
          return false;
        });
        return { ...snapshot, entries };
      });
      return removed;
    },
    purgeAcknowledged: async () => {
      let removed = 0;
      await store.mutate((snapshot) => {
        const entries = snapshot.entries.filter((entry) => entry.status !== 'acknowledged');
        removed = snapshot.entries.length - entries.length;
        // `lastClientSequence` is deliberately left alone. It is the high-water
        // mark of everything this device has ever sent, and the server's
        // checkpoint remembers it long after the entry is gone -- reissuing a
        // sequence gets the next sale refused as OUT_OF_ORDER.
        return { ...snapshot, entries };
      });
      return removed;
    },
  };
}

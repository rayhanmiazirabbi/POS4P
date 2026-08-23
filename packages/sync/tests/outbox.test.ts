import { describe, expect, it, vi } from 'vitest';

import {
  applyAcks,
  claimForUpload,
  createEventOutbox,
  createOutboxStore,
  createSyncEnvelope,
  describeIngestFailure,
  dueForUpload,
  envelopeFactory,
  flushOutbox,
  isPermanentIngestCode,
  toWireEnvelope,
  type IngestAck,
  type OutboxEntry,
  type OutboxStorage,
  type WireEnvelope,
} from '../src/index';

const context = { deviceId: 'device-1', organizationId: 'o1', storeId: 's1', userId: 'u1' } as const;
const saleEnvelope = envelopeFactory<{ cart: string }>(context, 'sale.create');

/** A storage port with a settling delay, so overlapping writes actually overlap. */
function slowStorage(delayMs = 2): OutboxStorage & { blob: () => string | null } {
  let blob: string | null = null;
  const wait = (): Promise<void> => new Promise((resolve) => setTimeout(resolve, delayMs));
  return {
    async read() {
      await wait();
      return blob;
    },
    async write(value) {
      await wait();
      blob = value;
    },
    blob: () => blob,
  };
}

describe('createOutboxStore', () => {
  it('keeps both sales when two are queued concurrently', async () => {
    // The previous implementation read the whole outbox, appended, and wrote it
    // back with no lock. Completing a sale and the reconnect flush overlap by
    // design, so the second write erased the first and a paid-for sale vanished
    // with no error anywhere.
    const store = createOutboxStore<{ cart: string }>(slowStorage());
    await Promise.all([
      store.queue((sequence) => saleEnvelope({ cart: 'paracetamol' }, sequence)),
      store.queue((sequence) => saleEnvelope({ cart: 'omeprazole' }, sequence)),
    ]);
    const snapshot = await store.snapshot();
    expect(snapshot.entries.map((entry) => entry.envelope.payload.cart)).toEqual(['paracetamol', 'omeprazole']);
  });

  it('gives concurrent sales distinct client sequences', async () => {
    // Two sales sharing a sequence is not a cosmetic problem: the server's
    // checkpoint refuses the lower one as OUT_OF_ORDER, permanently.
    const store = createOutboxStore<{ cart: string }>(slowStorage());
    await Promise.all(
      ['a', 'b', 'c', 'd'].map((cart) => store.queue((sequence) => saleEnvelope({ cart }, sequence))),
    );
    const snapshot = await store.snapshot();
    const sequences = snapshot.entries.map((entry) => entry.envelope.clientSequence);
    expect([...new Set(sequences)]).toHaveLength(4);
    expect(snapshot.lastClientSequence).toBe(4);
  });

  it('does not burn a client sequence when the envelope is rejected', async () => {
    const store = createOutboxStore<{ cart: string }>(slowStorage());
    await store.queue((sequence) => saleEnvelope({ cart: 'first' }, sequence));
    await expect(
      store.queue(() => {
        throw new Error('bad envelope');
      }),
    ).rejects.toThrow('bad envelope');
    // The counter advanced with the entry it numbered, not before it.
    expect((await store.snapshot()).lastClientSequence).toBe(1);
    const next = await store.queue((sequence) => saleEnvelope({ cart: 'second' }, sequence));
    expect(next.envelope.clientSequence).toBe(2);
  });

  it('keeps accepting sales after a failed write', async () => {
    // A wedged promise chain would stop the queue for the rest of the session,
    // which on a phone with no signal means every subsequent sale is lost.
    let failNext = true;
    const storage: OutboxStorage = {
      async read() {
        return null;
      },
      async write() {
        if (failNext) {
          failNext = false;
          throw new Error('disk full');
        }
      },
    };
    const store = createOutboxStore<{ cart: string }>(storage);
    await expect(store.queue((sequence) => saleEnvelope({ cart: 'lost' }, sequence))).rejects.toThrow('disk full');
    await expect(store.queue((sequence) => saleEnvelope({ cart: 'kept' }, sequence))).resolves.toMatchObject({
      status: 'pending',
    });
  });

  it('refuses to read a corrupt queue rather than reporting it empty', async () => {
    const storage: OutboxStorage = {
      async read() {
        return '{not json';
      },
      async write() {},
    };
    // Returning `[]` here would present a device holding unuploaded sales as
    // fully synced, and the next write would overwrite them for good.
    await expect(createOutboxStore(storage).snapshot()).rejects.toThrow(/unreadable/);
  });

  it('recovers the counter from the entries when the stored one lags', async () => {
    // A counter behind its own entries hands the next sale a sequence the server
    // has already passed, which it refuses as OUT_OF_ORDER forever.
    const entry = {
      envelope: createSyncEnvelope({ ...context, eventType: 'sale.create', clientSequence: 9, payload: {} }),
      status: 'pending' as const,
      attempts: 0,
      nextAttemptAt: null,
      error: null,
    };
    const storage: OutboxStorage = {
      async read() {
        return JSON.stringify({ entries: [entry], lastClientSequence: 0 });
      },
      async write() {},
    };
    expect((await createOutboxStore(storage).snapshot()).lastClientSequence).toBe(9);
  });

  it('reads the older bare-array layout instead of discarding it', async () => {
    const entry = {
      envelope: createSyncEnvelope({ ...context, eventType: 'sale.create', clientSequence: 4, payload: {} }),
      status: 'pending' as const,
      attempts: 0,
      nextAttemptAt: null,
      error: null,
    };
    const storage: OutboxStorage = {
      async read() {
        return JSON.stringify([entry]);
      },
      async write() {},
    };
    const snapshot = await createOutboxStore(storage).snapshot();
    expect(snapshot.entries).toHaveLength(1);
    expect(snapshot.lastClientSequence).toBe(4);
  });
});

describe('applyAcks', () => {
  function queued(...carts: string[]): OutboxEntry<{ cart: string }>[] {
    return carts.map((cart, index) => ({
      envelope: saleEnvelope({ cart }, index + 1),
      status: 'uploading' as const,
      attempts: 0,
      nextAttemptAt: null,
      error: null,
    }));
  }

  const now = '2026-08-22T10:00:00Z';

  it('acknowledges applied and duplicate events alike', () => {
    const outbox = queued('a', 'b');
    const acks: IngestAck[] = [
      { eventId: outbox[0]!.envelope.eventId, serverSequence: 5 },
      { eventId: outbox[1]!.envelope.eventId, serverSequence: 4, duplicate: true },
    ];
    const outcome = applyAcks(outbox, acks, outbox.map((e) => e.envelope.eventId), now);
    expect(outcome.outbox.map((entry) => entry.status)).toEqual(['acknowledged', 'acknowledged']);
    expect(outcome.applied).toHaveLength(1);
    expect(outcome.duplicates).toHaveLength(1);
  });

  it('retries a stock conflict instead of discarding the sale', () => {
    // This is the fix that matters most here. The client used to call
    // `rejectEvent` on every non-network failure, so an offline sale that drew
    // stock the server had not yet received was thrown away permanently -- even
    // though the delivery that reconciles it usually arrives the same week.
    const outbox = queued('paracetamol');
    const eventId = outbox[0]!.envelope.eventId;
    const outcome = applyAcks(outbox, [{ eventId, errorCode: 'INSUFFICIENT_STOCK' }], [eventId], now);
    expect(outcome.outbox[0]?.status).toBe('failed');
    expect(outcome.retrying).toEqual([eventId]);
    expect(dueForUpload(outcome.outbox, '2026-08-22T11:00:00Z')).toHaveLength(1);
  });

  it('rejects only what a retry can never fix, with a reason a counter can act on', () => {
    for (const code of ['IDENTITY_MISMATCH', 'UNSUPPORTED_EVENT_TYPE', 'VALIDATION_ERROR', 'OUT_OF_ORDER']) {
      const outbox = queued('a');
      const eventId = outbox[0]!.envelope.eventId;
      const outcome = applyAcks(outbox, [{ eventId, errorCode: code }], [eventId], now);
      expect(outcome.outbox[0]?.status, code).toBe('rejected');
      expect(outcome.outbox[0]?.error, code).toBe(describeIngestFailure(code));
      // Never silently dropped: a rejected entry stays for manual settlement.
      expect(outcome.outbox, code).toHaveLength(1);
    }
    expect(isPermanentIngestCode('CONFLICT')).toBe(false);
    expect(isPermanentIngestCode('INTERNAL_ERROR')).toBe(false);
  });

  it('says out loud that an out-of-order sale must be re-entered', () => {
    // The slot is gone, but the sale is real and unrecorded. "Rejected" alone
    // reads as "handled".
    expect(describeIngestFailure('OUT_OF_ORDER')).toMatch(/by hand/);
  });

  it('releases an uploaded event the server did not answer for', () => {
    // Left as `uploading`, the entry is invisible to `dueForUpload` and nothing
    // retries it until the app restarts.
    const outbox = queued('answered', 'ignored');
    const answered = outbox[0]!.envelope.eventId;
    const ignored = outbox[1]!.envelope.eventId;
    const outcome = applyAcks(outbox, [{ eventId: answered, serverSequence: 1 }], [answered, ignored], now);
    expect(outcome.released).toEqual([ignored]);
    expect(outcome.outbox[1]?.status).toBe('pending');
    expect(dueForUpload(outcome.outbox, now).map((entry) => entry.envelope.eventId)).toEqual([ignored]);
  });

  it('leaves entries that were never claimed alone', () => {
    const outbox = queued('a');
    const claimed = claimForUpload(outbox, outbox[0]!.envelope.eventId);
    const outcome = applyAcks(claimed.outbox as OutboxEntry<{ cart: string }>[], [], [], now);
    expect(outcome.outbox[0]?.status).toBe('uploading');
    expect(outcome.released).toEqual([]);
  });

  it('backs off further on each successive retry', () => {
    const outbox = queued('a');
    const eventId = outbox[0]!.envelope.eventId;
    const backoff = { baseMs: 1000, maxMs: 60_000, jitterRatio: 0 };
    const first = applyAcks(outbox, [{ eventId, errorCode: 'INTERNAL_ERROR' }], [eventId], now, backoff, () => 0.5);
    expect(first.outbox[0]?.nextAttemptAt).toBe('2026-08-22T10:00:01.000Z');
    const second = applyAcks(first.outbox, [{ eventId, errorCode: 'INTERNAL_ERROR' }], [eventId], now, backoff, () => 0.5);
    expect(second.outbox[0]?.attempts).toBe(2);
    expect(second.outbox[0]?.nextAttemptAt).toBe('2026-08-22T10:00:02.000Z');
  });
});

describe('toWireEnvelope', () => {
  it('sends the identity fields and nothing the server forbids', () => {
    // `SyncEventEnvelopeIn` sets `extra="forbid"`, so one stray local field --
    // `idempotencyKey` above all -- is a 422 for the whole batch, including the
    // well-formed sales beside it.
    const wire: WireEnvelope<{ cart: string }> = toWireEnvelope(saleEnvelope({ cart: 'a' }, 3, '2026-08-18T09:30:00Z'));
    expect(Object.keys(wire).sort()).toEqual(
      ['clientSequence', 'createdAt', 'deviceId', 'eventId', 'eventType', 'organizationId', 'payload', 'storeId', 'userId'],
    );
    expect(wire).not.toHaveProperty('idempotencyKey');
    // Sent so ingest can refuse a queue flushed against the wrong store, rather
    // than booking it against whichever store the token happens to name.
    expect(wire).toMatchObject({ ...context, clientSequence: 3, createdAt: '2026-08-18T09:30:00Z' });
  });
});

describe('flushOutbox', () => {
  const now = '2026-08-22T10:00:00Z';

  async function seeded(...carts: string[]) {
    const store = createOutboxStore<{ cart: string }>(slowStorage(0));
    for (const cart of carts) await store.queue((sequence) => saleEnvelope({ cart }, sequence));
    return store;
  }

  function acceptAll(seen: WireEnvelope<{ cart: string }>[][]) {
    return async (events: readonly WireEnvelope<{ cart: string }>[]): Promise<readonly IngestAck[]> => {
      seen.push([...events]);
      return events.map((event, index) => ({ eventId: event.eventId, serverSequence: index + 1 }));
    };
  }

  it('uploads every due sale and marks them acknowledged', async () => {
    const store = await seeded('a', 'b');
    const seen: WireEnvelope<{ cart: string }>[][] = [];
    const summary = await flushOutbox(store, acceptAll(seen), { nowUtcIso: now });
    expect(summary).toMatchObject({ uploaded: 2, duplicates: 0, rejected: 0, remaining: 0, offline: false, firstError: null });
    expect((await store.snapshot()).entries.every((entry) => entry.status === 'acknowledged')).toBe(true);
  });

  it('uploads in client-sequence order, batched', async () => {
    // The server's checkpoint only moves forward, so a later sequence arriving
    // first makes the earlier one permanently OUT_OF_ORDER.
    const store = await seeded('a', 'b', 'c', 'd', 'e');
    const seen: WireEnvelope<{ cart: string }>[][] = [];
    await flushOutbox(store, acceptAll(seen), { nowUtcIso: now, batchLimit: 2 });
    expect(seen.map((batch) => batch.map((event) => event.clientSequence))).toEqual([[1, 2], [3, 4], [5]]);
  });

  it('does not re-send an event the server asked us to retry', async () => {
    // Without a per-flush guard the retried entry is due again immediately, so the
    // next batch of the same loop picks it straight back up and spins.
    const store = await seeded('a');
    let calls = 0;
    const summary = await flushOutbox(
      store,
      async (events) => {
        calls += 1;
        return events.map((event) => ({ eventId: event.eventId, errorCode: 'INSUFFICIENT_STOCK' }));
      },
      { nowUtcIso: now },
    );
    expect(calls).toBe(1);
    expect(summary).toMatchObject({ uploaded: 0, retrying: 1, rejected: 0, remaining: 1 });
    expect((await store.snapshot()).entries[0]?.status).toBe('failed');
  });

  it('keeps a paid sale the server refused, and says why', async () => {
    const store = await seeded('a');
    const summary = await flushOutbox(
      store,
      async (events) => events.map((event) => ({ eventId: event.eventId, errorCode: 'OUT_OF_ORDER' })),
      { nowUtcIso: now },
    );
    expect(summary.rejected).toBe(1);
    expect(summary.firstError).toMatch(/by hand/);
    // Rejected entries are excluded from `remaining` but never deleted: they are
    // the only record that the sale happened at all.
    expect(summary.remaining).toBe(0);
    expect((await store.snapshot()).entries).toHaveLength(1);
  });

  it('lets the rest of a batch through when one event is refused', async () => {
    const store = await seeded('good', 'bad');
    const summary = await flushOutbox(
      store,
      async (events) =>
        events.map((event) => (event.payload.cart === 'bad' ? { eventId: event.eventId, errorCode: 'VALIDATION_ERROR' } : { eventId: event.eventId, serverSequence: 1 })),
      { nowUtcIso: now },
    );
    expect(summary).toMatchObject({ uploaded: 1, rejected: 1 });
    expect((await store.snapshot()).entries.map((entry) => entry.status)).toEqual(['acknowledged', 'rejected']);
  });

  it('puts the whole batch back in line when the upload never lands', async () => {
    const store = await seeded('a', 'b');
    const summary = await flushOutbox(
      store,
      async () => {
        throw Object.assign(new Error('Network request failed'), { code: 'NETWORK_ERROR' });
      },
      { nowUtcIso: now },
    );
    expect(summary).toMatchObject({ uploaded: 0, retrying: 2, rejected: 0, remaining: 2, offline: true });
    const entries = (await store.snapshot()).entries;
    // Not left as `uploading`: that status is invisible to `dueForUpload`, so the
    // sales would sit unretried until the app was restarted.
    expect(entries.every((entry) => entry.status === 'failed' && entry.nextAttemptAt !== null)).toBe(true);
  });

  it('stops after a failed batch rather than burning a backoff step on each one', async () => {
    const store = await seeded('a', 'b', 'c', 'd');
    let calls = 0;
    await flushOutbox(
      store,
      async () => {
        calls += 1;
        throw Object.assign(new Error('offline'), { code: 'NETWORK_ERROR' });
      },
      { nowUtcIso: now, batchLimit: 1 },
    );
    expect(calls).toBe(1);
    expect((await store.snapshot()).entries.filter((entry) => entry.status === 'pending')).toHaveLength(3);
  });

  it('treats an already-applied event as done, not as revenue', async () => {
    const store = await seeded('a');
    const summary = await flushOutbox(
      store,
      async (events) => events.map((event) => ({ eventId: event.eventId, serverSequence: 7, duplicate: true })),
      { nowUtcIso: now },
    );
    expect(summary).toMatchObject({ uploaded: 0, duplicates: 1, remaining: 0 });
  });

  it('retries an uploaded event the server never answered for', async () => {
    const store = await seeded('answered', 'ignored');
    const summary = await flushOutbox(
      store,
      async (events) => [{ eventId: events[0]!.eventId, serverSequence: 1 }],
      { nowUtcIso: now },
    );
    expect(summary).toMatchObject({ uploaded: 1, retrying: 1, remaining: 1 });
    expect((await store.snapshot()).entries[1]?.status).toBe('pending');
  });

  it('sends nothing when the queue is empty', async () => {
    const store = await seeded();
    let calls = 0;
    const summary = await flushOutbox(
      store,
      async () => {
        calls += 1;
        return [];
      },
      { nowUtcIso: now },
    );
    expect(calls).toBe(0);
    expect(summary).toMatchObject({ uploaded: 0, remaining: 0, firstError: null });
  });

  it('does not add a sale completed mid-flush to the batch already in flight', async () => {
    // Claiming happens inside the serialized read, so the in-flight batch is fixed
    // before the request leaves. The late sale is picked up by the next batch --
    // which is what should happen -- but it must not join a request whose acks have
    // already been shaped, or it would be marked acknowledged without being sent.
    const store = await seeded('a');
    let release = (): void => {};
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    const seen: WireEnvelope<{ cart: string }>[][] = [];
    const flushing = flushOutbox(
      store,
      async (events) => {
        seen.push([...events]);
        if (seen.length === 1) await gate;
        return events.map((event) => ({ eventId: event.eventId, serverSequence: seen.length }));
      },
      { nowUtcIso: now },
    );
    await store.queue((sequence) => saleEnvelope({ cart: 'late' }, sequence));
    release();
    const summary = await flushing;
    expect(seen.map((batch) => batch.map((event) => event.payload.cart))).toEqual([['a'], ['late']]);
    // The late sale took the next sequence rather than sharing the one in flight.
    expect(seen[1]?.[0]?.clientSequence).toBe(2);
    expect(summary).toMatchObject({ uploaded: 2, remaining: 0 });
  });
});

describe('createEventOutbox', () => {
  const now = '2026-08-22T10:00:00Z';

  function outbox() {
    return createEventOutbox<{ cart: string }>(slowStorage(0), 'sale.create');
  }

  it('queues, uploads and settles a sale through one surface', async () => {
    const queue = outbox();
    await queue.queue({ cart: 'a' }, context);
    expect(await queue.status()).toMatchObject({ pending: 1, retrying: 0, stuck: [] });
    const summary = await queue.flush(async (events) => events.map((event) => ({ eventId: event.eventId, serverSequence: 1 })), { nowUtcIso: now });
    expect(summary).toMatchObject({ uploaded: 1, remaining: 0 });
    expect(await queue.status()).toMatchObject({ pending: 0 });
  });

  it('dates the sale when it was rung up, not when it was uploaded', async () => {
    const queue = outbox();
    const entry = await queue.queue({ cart: 'a' }, context, '2026-08-18T09:30:00Z');
    expect(entry.envelope.createdAt).toBe('2026-08-18T09:30:00Z');
  });

  it('reports a refused sale as stuck, with the payload intact', async () => {
    // The payload is the shop's only record of a sale that was paid for and never
    // recorded, so it must survive the rejection.
    const queue = outbox();
    await queue.queue({ cart: 'paracetamol' }, context);
    await queue.flush(async (events) => events.map((event) => ({ eventId: event.eventId, errorCode: 'OUT_OF_ORDER' })), { nowUtcIso: now });
    const status = await queue.status();
    expect(status.pending).toBe(0);
    expect(status.stuck).toHaveLength(1);
    expect(status.stuck[0]?.payload).toEqual({ cart: 'paracetamol' });
    expect(status.stuck[0]?.reason).toMatch(/by hand/);
  });

  it('reports when the next retry is due, not merely that one is', async () => {
    const queue = outbox();
    await queue.queue({ cart: 'a' }, context);
    await queue.flush(async (events) => events.map((event) => ({ eventId: event.eventId, errorCode: 'INTERNAL_ERROR' })), {
      nowUtcIso: now,
      backoff: { baseMs: 1000, maxMs: 60_000, jitterRatio: 0 },
    });
    expect(await queue.status()).toMatchObject({ pending: 1, retrying: 1, nextRetryAt: '2026-08-22T10:00:01.000Z' });
  });

  it('refuses to forget a sale the server has not taken yet', async () => {
    // The clear button sits beside the refused sales. A mis-tap must not be able
    // to destroy the only copy of one that is still queued.
    const queue = outbox();
    const entry = await queue.queue({ cart: 'a' }, context);
    expect(await queue.forget(entry.envelope.eventId)).toBe(false);
    expect((await queue.status()).pending).toBe(1);
  });

  it('forgets a refused sale once someone has dealt with it', async () => {
    const queue = outbox();
    const entry = await queue.queue({ cart: 'a' }, context);
    await queue.flush(async (events) => events.map((event) => ({ eventId: event.eventId, errorCode: 'VALIDATION_ERROR' })), { nowUtcIso: now });
    expect(await queue.forget(entry.envelope.eventId)).toBe(true);
    expect((await queue.status()).stuck).toEqual([]);
  });

  it('keeps the sequence counter when purging accepted sales', async () => {
    // The server's checkpoint outlives the local entry. Restarting the counter
    // gets the next sale refused as OUT_OF_ORDER for good.
    const queue = outbox();
    await queue.queue({ cart: 'a' }, context);
    await queue.queue({ cart: 'b' }, context);
    await queue.flush(async (events) => events.map((event) => ({ eventId: event.eventId, serverSequence: 1 })), { nowUtcIso: now });
    expect(await queue.purgeAcknowledged()).toBe(2);
    const next = await queue.queue({ cart: 'c' }, context);
    expect(next.envelope.clientSequence).toBe(3);
  });

  it('returns a sale stranded mid-upload to the queue', async () => {
    // `uploading` is invisible to the flush, so without this a sale killed in
    // flight sits there forever while the screen reports nothing owed.
    const queue = outbox();
    await queue.queue({ cart: 'a' }, context);
    await queue.flush(async () => {
      throw Object.assign(new Error('killed'), { code: 'NETWORK_ERROR' });
    }, { nowUtcIso: now });
    await queue.store.mutate((snapshot) => ({
      ...snapshot,
      entries: snapshot.entries.map((entry) => ({ ...entry, status: 'uploading' as const })),
    }));
    await queue.recover();
    expect((await queue.store.snapshot()).entries[0]?.status).toBe('pending');
    expect((await queue.status()).pending).toBe(1);
  });
});

describe('envelopeFactory', () => {
  it('stamps every identity field so a caller cannot omit one', () => {
    const envelope = saleEnvelope({ cart: 'a' }, 1);
    expect(envelope).toMatchObject({ ...context, eventType: 'sale.create', clientSequence: 1 });
  });

  it('preserves the moment the sale happened when one is supplied', () => {
    // An offline sale rung up on Tuesday must not be dated Thursday just because
    // that is when the phone found signal.
    const envelope = saleEnvelope({ cart: 'a' }, 1, '2026-08-18T09:30:00Z');
    expect(envelope.createdAt).toBe('2026-08-18T09:30:00Z');
  });

  it('falls back to now when no moment is given', () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-08-22T10:00:00Z'));
    expect(saleEnvelope({ cart: 'a' }, 1).createdAt).toBe('2026-08-22T10:00:00.000Z');
    vi.useRealTimers();
  });
});

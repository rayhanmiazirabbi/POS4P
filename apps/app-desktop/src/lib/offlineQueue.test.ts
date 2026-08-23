import { beforeEach, describe, expect, it } from 'vitest';

import type { SaleCreateRequest } from '@pharmacy/api';
import type { IngestAck } from '@pharmacy/sync';

import { AUTO_FLUSH_INTERVAL_MS, enqueueFlush, flushQueue, forgetSale, queueSale, queueStatus, recoverOutbox, uploadsDue } from './offlineQueue';
import { desktopPlatform } from '../platform/runtime';

/**
 * Outside Tauri the platform store falls back to an in-process map, which is a real
 * store for these purposes -- the queue's contract is about what survives a flush,
 * not about which backend holds it.
 */
async function resetQueue(): Promise<void> {
  const store = (await desktopPlatform()).database;
  await store.remove('desktop_sale_outbox_v1');
  await store.remove('desktop_sale_queue_v1');
}

function body(total: string): SaleCreateRequest {
  return { items: [{ storeProductId: 'p1', quantity: '1' }], payments: [{ method: 'cash', amount: total, receivedAmount: total }], total };
}

const context = {
  deviceId: '11111111-1111-4111-8111-111111111111',
  organizationId: '22222222-2222-4222-8222-222222222222',
  storeId: '33333333-3333-4333-8333-333333333333',
  userId: '44444444-4444-4444-8444-444444444444',
};

const accept = (events: readonly { eventId: string }[]): Promise<IngestAck[]> =>
  Promise.resolve(events.map((event, index) => ({ eventId: event.eventId, serverSequence: index + 1 })));

const refuse = (code: string) => (events: readonly { eventId: string }[]): Promise<IngestAck[]> =>
  Promise.resolve(events.map((event) => ({ eventId: event.eventId, errorCode: code })));

const networkFailure = (): never => {
  throw Object.assign(new Error('Network unreachable'), { code: 'NETWORK_ERROR' });
};

describe('desktop offline sale queue', () => {
  beforeEach(resetQueue);

  it('drops an entry only once the server has acknowledged it', async () => {
    await queueSale(body('10.00'), context);
    expect((await queueStatus()).pending).toBe(1);

    const result = await flushQueue(accept);

    expect(result.uploaded).toBe(1);
    expect(result.rejected).toBe(0);
    expect(result.remaining).toBe(0);
    expect((await queueStatus()).pending).toBe(0);
  });

  it('sends the identity fields the ingest endpoint requires, and nothing local', async () => {
    // `SyncEventEnvelopeIn` forbids unknown keys, so one extra field is a 422 for
    // the whole batch -- including the well-formed sales beside it.
    await queueSale(body('10.00'), context);

    let sent: readonly Record<string, unknown>[] = [];
    await flushQueue(async (events) => {
      sent = events as unknown as readonly Record<string, unknown>[];
      return accept(events);
    });

    expect(Object.keys(sent[0] ?? {}).sort()).toEqual([
      'clientSequence', 'createdAt', 'deviceId', 'eventId', 'eventType', 'organizationId', 'payload', 'storeId', 'userId',
    ]);
    expect(sent[0]?.storeId).toBe(context.storeId);
    expect(sent[0]?.deviceId).toBe(context.deviceId);
  });

  it('numbers sales in the order they were rung up', async () => {
    await queueSale(body('10.00'), context);
    await queueSale(body('20.00'), context);

    const seen: number[] = [];
    await flushQueue(async (events) => {
      seen.push(...events.map((event) => event.clientSequence));
      return accept(events);
    });

    expect(seen).toEqual([1, 2]);
  });

  it('keeps retrying a sale the server refuses over stock rather than discarding it', async () => {
    // The sale happened: cash crossed the counter and stock left the shelf. The
    // shop may not have booked in the batch it drew down yet, so the same envelope
    // applies unchanged once the delivery is entered. The previous implementation
    // marked any non-network error permanent and threw the sale away.
    await queueSale(body('10.00'), context);

    const result = await flushQueue(refuse('INSUFFICIENT_STOCK'));

    expect(result.rejected).toBe(0);
    expect(result.retrying).toBe(1);
    expect(result.remaining).toBe(1);
    const status = await queueStatus();
    expect(status.stuck).toEqual([]);
    expect(status.pending).toBe(1);
    expect(status.nextRetryAt).not.toBeNull();
  });

  it('parks a sale the server will never accept, with the payload and a reason', async () => {
    await queueSale(body('10.00'), context);

    const result = await flushQueue(refuse('OUT_OF_ORDER'));

    expect(result.rejected).toBe(1);
    const status = await queueStatus();
    // Excluded from the pending count: no retry will fix it, so counting it as
    // "queued" would show a badge that never clears.
    expect(status.pending).toBe(0);
    expect(status.stuck).toHaveLength(1);
    expect(status.stuck[0]?.payload.total).toBe('10.00');
    expect(status.stuck[0]?.reason).toContain('re-entered by hand');
  });

  it('never re-sends a sale it has already parked', async () => {
    await queueSale(body('10.00'), context);
    await flushQueue(refuse('OUT_OF_ORDER'));

    let attempts = 0;
    await flushQueue(async (events) => {
      attempts += events.length;
      return accept(events);
    });

    expect(attempts).toBe(0);
  });

  it('puts the whole batch back in line on a network failure', async () => {
    await queueSale(body('10.00'), context);
    await queueSale(body('20.00'), context);

    const result = await flushQueue(async () => networkFailure());

    expect(result.offline).toBe(true);
    expect(result.rejected).toBe(0);
    expect(result.remaining).toBe(2);
    expect((await queueStatus()).stuck).toEqual([]);
  });

  it('counts a duplicate as settled but not as revenue', async () => {
    // A batch that reached the server before the connection dropped is replayed on
    // the next flush. The server says duplicate; the till must not report it as a
    // second sale, and must not keep it either.
    await queueSale(body('10.00'), context);

    const result = await flushQueue(async (events) => events.map((event) => ({ eventId: event.eventId, serverSequence: 1, duplicate: true })));

    expect(result.uploaded).toBe(0);
    expect(result.duplicates).toBe(1);
    expect(result.remaining).toBe(0);
  });

  it('refuses to forget a sale that is still owed to the server', async () => {
    // `forgetSale` is reachable from a button. If it could delete a pending entry,
    // one stray click would destroy a sale that was about to upload fine.
    const eventId = await queueSale(body('10.00'), context);

    expect(await forgetSale(eventId)).toBe(false);
    expect((await queueStatus()).pending).toBe(1);
  });

  it('forgets a parked sale once someone has re-entered it', async () => {
    await queueSale(body('10.00'), context);
    await flushQueue(refuse('OUT_OF_ORDER'));
    const parked = (await queueStatus()).stuck[0];

    expect(await forgetSale(parked?.eventId ?? 'unknown')).toBe(true);
    expect((await queueStatus()).stuck).toEqual([]);
  });

  it('returns a sale stranded mid-upload by a killed app to the queue', async () => {
    // An app killed between claiming an entry and hearing back leaves it
    // `uploading` on disk, and no later flush will pick that up -- `dueForUpload`
    // only considers pending and failed. Seeded directly because that is precisely
    // the on-disk state; a flush that throws is handled and schedules a retry
    // instead, so it cannot produce this.
    await queueSale(body('10.00'), context);
    const store = (await desktopPlatform()).database;
    const stranded = (await store.get('desktop_sale_outbox_v1'))?.replace('"status":"pending"', '"status":"uploading"');
    await store.set('desktop_sale_outbox_v1', stranded ?? '');
    expect(await flushQueue(accept)).toMatchObject({ uploaded: 0, remaining: 1 });

    await recoverOutbox();

    expect(await flushQueue(accept)).toMatchObject({ uploaded: 1, remaining: 0 });
  });

  it('adopts sales left by the previous queue so they are not silently orphaned', async () => {
    // The old implementation wrote a bare array under its own key with no device,
    // store or user. Those cannot be uploaded -- stamping them with whoever is
    // signed in now would book them at the wrong store -- but they are money taken
    // at the counter, so they surface for a person instead of disappearing.
    const store = (await desktopPlatform()).database;
    await store.set(
      'desktop_sale_queue_v1',
      JSON.stringify([{ id: 'legacy-1', body: body('99.00'), createdAt: '2026-01-01T00:00:00.000Z' }]),
    );

    expect(await recoverOutbox()).toBe(1);

    const status = await queueStatus();
    expect(status.pending).toBe(0);
    expect(status.stuck).toHaveLength(1);
    expect(status.stuck[0]?.payload.total).toBe('99.00');
    expect(status.stuck[0]?.createdAt).toBe('2026-01-01T00:00:00.000Z');
    // The old key is cleared, so a second pass does not adopt them twice.
    expect(await recoverOutbox()).toBe(0);
    expect((await queueStatus()).stuck).toHaveLength(1);
  });

  it('does not let an adopted legacy sale take a client sequence a real sale needs', async () => {
    const store = (await desktopPlatform()).database;
    await store.set(
      'desktop_sale_queue_v1',
      JSON.stringify([{ id: 'legacy-1', body: body('99.00'), createdAt: '2026-01-01T00:00:00.000Z' }]),
    );
    await recoverOutbox();

    await queueSale(body('10.00'), context);

    const seen: number[] = [];
    await flushQueue(async (events) => {
      seen.push(...events.map((event) => event.clientSequence));
      return accept(events);
    });
    // Only the real sale is sent, and it is the device's first: the adopted entry
    // never had a sequence and must not consume one.
    expect(seen).toEqual([1]);
  });

  it('keeps the automatic flush cadence inside the planned 20-30s window', () => {
    expect(AUTO_FLUSH_INTERVAL_MS).toBeGreaterThanOrEqual(20_000);
    expect(AUTO_FLUSH_INTERVAL_MS).toBeLessThanOrEqual(30_000);
  });

  it('calls an upload due only once its backoff has elapsed, like the engine does', async () => {
    expect(await uploadsDue()).toBe(false);

    await queueSale(body('10.00'), context);
    expect(await uploadsDue('2026-01-01T00:00:00Z')).toBe(true);

    // A network failure schedules the engine's own retry one base step out; the
    // automatic flush respects that instead of posting blind every tick.
    await flushQueue(async () => networkFailure(), '2026-01-01T00:00:00Z');
    expect(await uploadsDue('2026-01-01T00:00:00.500Z')).toBe(false);
    expect(await uploadsDue('2026-01-01T00:00:02Z')).toBe(true);
  });

  it('skips an automatic flush when nothing is due and posts no duplicate batch', async () => {
    await queueSale(body('10.00'), context);
    const batches: number[] = [];
    const counting = async (events: readonly { eventId: string }[]): Promise<IngestAck[]> => {
      batches.push(events.length);
      return accept(events);
    };

    const first = enqueueFlush(counting, { onlyIfDue: true });
    // Started while the first was still in flight: serialized behind it, finds
    // nothing left due, and sends nothing of its own.
    const second = enqueueFlush(counting, { onlyIfDue: true });

    expect(await first).toMatchObject({ uploaded: 1, remaining: 0 });
    expect(await second).toBeNull();
    expect(batches).toEqual([1]);
    expect((await queueStatus()).pending).toBe(0);
  });

  it('runs a manual flush through the same chain after any in-flight automatic one', async () => {
    await queueSale(body('10.00'), context);
    const order: string[] = [];
    const tag = (label: string) => async (events: readonly { eventId: string }[]): Promise<IngestAck[]> => {
      order.push(`${label}:${events.length}`);
      return accept(events);
    };

    // The server never answers this batch, so the sale is released straight back
    // to pending -- leaving real work for the manual flush that was started
    // while the automatic one was still in flight.
    const automatic = enqueueFlush(async (events) => {
      order.push(`automatic:${events.length}`);
      return [];
    }, { onlyIfDue: true });
    const manual = enqueueFlush(tag('manual'));

    expect(await automatic).toMatchObject({ uploaded: 0, retrying: 1 });
    expect(await manual).toMatchObject({ uploaded: 1, remaining: 0 });
    // Serialized on the chain: the manual flush looked at the queue only after
    // the automatic one had settled, and no event was posted twice.
    expect(order).toEqual(['automatic:1', 'manual:1']);
  });
});

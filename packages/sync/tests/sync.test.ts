import { describe, expect, it } from 'vitest';
import {
  acknowledge,
  advanceCursor,
  claimForUpload,
  computeBackoffMs,
  createSyncEnvelope,
  dueForUpload,
  enqueue,
  findSequenceGaps,
  initialCursor,
  markFailed,
  partitionRemoteChanges,
  recoverAfterRestart,
  rejectEvent,
  releaseUpload,
  retryDelayMs,
  scheduleRetry,
  sortRemoteChanges,
  summarize,
  validateEnvelope,
  type OutboxEntry,
  type SyncEnvelope,
} from '../src/index';

const base: SyncEnvelope<{ saleId: string }> = {
  eventId: 'e1',
  idempotencyKey: 'device:1:event:1',
  deviceId: 'd1',
  organizationId: 'o1',
  storeId: 's1',
  userId: 'u1',
  eventType: 'sale.create',
  createdAt: '2026-08-21T00:00:00Z',
  clientSequence: 1,
  payload: { saleId: 'sale1' },
};

function frozenOutbox(...entries: OutboxEntry[]): readonly OutboxEntry[] { return Object.freeze(entries); }

describe('sync', () => {
  it('deduplicates enqueue and acknowledges only after server response', () => {
    const pending = enqueue([], base);
    expect(enqueue(pending, base)).toHaveLength(1);
    expect(pending[0]?.status).toBe('pending');
    expect(acknowledge(pending, { eventId: 'e1', serverSequence: 8, duplicate: false })[0]?.status).toBe('acknowledged');
  });

  it('preserves actionable failures and uses server ordering', () => {
    const failed = markFailed(enqueue([], base), 'e1', 'stock conflict', '2026-08-21T00:01:00Z');
    expect(failed[0]).toMatchObject({ status: 'failed', attempts: 1, error: 'stock conflict' });
    expect(sortRemoteChanges([{ serverSequence: 2, eventType: 'b', payload: {} }, { serverSequence: 1, eventType: 'a', payload: {} }]).map((change) => change.serverSequence)).toEqual([1, 2]);
    expect(retryDelayMs(10)).toBe(60000);
  });

  it('validates envelopes against the offline mutation contract', () => {
    expect(validateEnvelope(base)).toEqual([]);
    expect(validateEnvelope({ ...base, eventId: '', createdAt: '2026-08-21T00:00:00', clientSequence: 0 })).toEqual([
      'eventId is required',
      'createdAt must be a UTC ISO timestamp',
      'clientSequence must be a positive integer',
    ]);
  });

  it('creates envelopes with deterministic defaults and rejects invalid ones', () => {
    const envelope = createSyncEnvelope({ deviceId: 'd9', organizationId: 'o9', storeId: 's9', userId: 'u9', eventType: 'sale.create', clientSequence: 3, payload: {} });
    expect(envelope.eventId).toBeTruthy();
    expect(envelope.idempotencyKey).toBe('d9:3');
    expect(() => createSyncEnvelope({ ...envelope, clientSequence: 0 })).toThrow(/clientSequence/);
  });

  it('crash during enqueue replays safely: the same event is never queued twice', () => {
    const first = Object.freeze(enqueue([], base));
    expect(enqueue(first, base)).toHaveLength(1);
    const sameOperationNewEventId = { ...base, eventId: 'e1-retry', payload: { saleId: 'sale1-b' } };
    expect(enqueue(first, sameOperationNewEventId)).toHaveLength(1);
    const distinctOperation = { ...base, eventId: 'e2', idempotencyKey: 'device:1:event:2', clientSequence: 2 };
    expect(enqueue(first, distinctOperation)).toHaveLength(2);
    expect(first).toHaveLength(1);
  });

  it('claims exclusively so an in-flight event is never uploaded twice concurrently', () => {
    const outbox = enqueue([], base);
    const first = claimForUpload(outbox, 'e1');
    expect(first.entry?.status).toBe('uploading');
    const second = claimForUpload(first.outbox, 'e1');
    expect(second.entry).toBeNull();
    expect(claimForUpload(outbox, 'missing').entry).toBeNull();
  });

  it('survives offline/reconnect: failures keep their history and resume when due', () => {
    let outbox = enqueue([], base);
    const claimed = claimForUpload(outbox, 'e1');
    outbox = claimed.outbox;
    const failure = scheduleRetry(outbox, 'e1', 'network unreachable', '2026-08-21T00:00:00Z');
    outbox = failure.outbox;
    expect(failure.retried).toBe(true);
    expect(outbox[0]).toMatchObject({ status: 'failed', attempts: 1, nextAttemptAt: '2026-08-21T00:00:01.000Z', error: 'network unreachable' });
    expect(dueForUpload(outbox, '2026-08-21T00:00:00Z')).toHaveLength(0);
    expect(dueForUpload(outbox, '2026-08-21T00:00:01Z')[0]?.envelope.eventId).toBe('e1');

    const crashed = claimForUpload(dueForUpload(outbox, '2026-08-21T00:01:00Z'), 'e1').outbox;
    const recovered = recoverAfterRestart(crashed);
    expect(recovered[0]?.status).toBe('pending');
    expect(recovered[0]?.attempts).toBe(1);
    expect(dueForUpload(recovered, '2026-08-21T00:00:00Z')[0]?.envelope.eventId).toBe('e1');
  });

  it('releases uploads that crash mid-flight without losing the reason', () => {
    const outbox = claimForUpload(enqueue([], base), 'e1').outbox;
    const released = releaseUpload(outbox, 'e1', 'process killed mid-upload');
    expect(released[0]).toMatchObject({ status: 'pending', error: 'process killed mid-upload' });
    expect(releaseUpload(released, 'other')[0]?.status).toBe('pending');
    expect(releaseUpload(outbox, 'e1')[0]?.error).toBeNull();
  });

  it('treats duplicate uploads as acknowledged exactly once', () => {
    const outbox = Object.freeze(enqueue([], base));
    const ack = { eventId: 'e1', serverSequence: 8, duplicate: true };
    const once = acknowledge(outbox, ack);
    expect(once[0]?.status).toBe('acknowledged');
    expect(acknowledge(once, ack)).toEqual(once);
    expect(outbox[0]?.status).toBe('pending');
  });

  it('never re-schedules rejected events (revoked device)', () => {
    const outbox = rejectEvent(enqueue([], base), 'e1', 'device revoked by organization');
    expect(outbox[0]).toMatchObject({ status: 'rejected', nextAttemptAt: null });
    expect(dueForUpload(outbox, '2099-01-01T00:00:00Z')).toHaveLength(0);
    expect(scheduleRetry(outbox, 'e1', 'retry anyway', '2026-08-21T00:00:00Z')).toMatchObject({ retried: false });
    expect(scheduleRetry(outbox, 'e1', 'retry anyway', '2026-08-21T00:00:00Z').outbox[0]?.status).toBe('rejected');
    expect(claimForUpload(outbox, 'e1').entry).toBeNull();
    expect(rejectEvent(outbox, 'e1', 'again')[0]?.error).toBe('device revoked by organization');
  });

  it('orders remote changes by server sequence, ignoring arrival order and time', () => {
    const cursor = initialCursor();
    const page = [{ serverSequence: 7, eventType: 'c', payload: {} }, { serverSequence: 5, eventType: 'b', payload: {} }];
    const advanced = advanceCursor(cursor, page);
    expect(sortRemoteChanges(page).map((change) => change.serverSequence)).toEqual([5, 7]);
    expect(partitionRemoteChanges(cursor, page).fresh).toHaveLength(2);
    expect(partitionRemoteChanges(advanced, page).fresh).toHaveLength(0);

    const lateOldPage = [{ serverSequence: 3, eventType: 'a', payload: {} }];
    expect(advanceCursor(advanced, lateOldPage).lastServerSequence).toBe(7);
    const partitioned = partitionRemoteChanges(advanced, [...lateOldPage, { serverSequence: 8, eventType: 'd', payload: {} }]);
    expect(partitioned.fresh.map((change) => change.serverSequence)).toEqual([8]);
    expect(partitioned.duplicates.map((change) => change.serverSequence)).toEqual([3]);
    expect(advanceCursor(initialCursor(), [])).toEqual({ lastServerSequence: 0 });
  });

  it('reports cursor gaps instead of silently skipping missed events', () => {
    const page = [{ serverSequence: 4, eventType: 'd', payload: {} }, { serverSequence: 1, eventType: 'a', payload: {} }, { serverSequence: 2, eventType: 'b', payload: {} }, { serverSequence: 4, eventType: 'd2', payload: {} }];
    expect(findSequenceGaps(page)).toEqual([3]);
    expect(findSequenceGaps([{ serverSequence: 5, eventType: 'e', payload: {} }])).toEqual([]);
    const advanced = advanceCursor(initialCursor(), page);
    expect(advanced.lastServerSequence).toBe(4);
    expect(findSequenceGaps([{ serverSequence: 9, eventType: 'i', payload: {} }, { serverSequence: 2, eventType: 'b', payload: {} }])).toEqual([3, 4, 5, 6, 7, 8]);
  });

  it('backs off exponentially with optional jitter and a hard cap', () => {
    expect(computeBackoffMs(1)).toBe(1000);
    expect(computeBackoffMs(2)).toBe(2000);
    expect(computeBackoffMs(7)).toBe(60000);
    expect(computeBackoffMs(1, { baseMs: 250, maxMs: 4000, jitterRatio: 0.5 }, () => 0)).toBe(125);
    expect(computeBackoffMs(1, { baseMs: 250, maxMs: 4000, jitterRatio: 0.5 }, () => 1)).toBe(375);
    expect(computeBackoffMs(1, { baseMs: 250, maxMs: 4000, jitterRatio: 0.5 }, () => 0.5)).toBe(250);
    expect(computeBackoffMs(20, { baseMs: 1000, maxMs: 60000, jitterRatio: 1 }, () => 1)).toBeLessThanOrEqual(60000);
    expect(() => computeBackoffMs(0)).toThrow('Attempts must be a positive integer');
    expect(() => retryDelayMs(-1)).toThrow('Invalid retry attempt');
  });

  it('summarizes sync state including connectivity and the earliest retry', () => {
    const entry = (eventId: string, clientSequence: number, status: OutboxEntry['status'], overrides: Partial<OutboxEntry> = {}): OutboxEntry => ({ envelope: { ...base, eventId, clientSequence }, status, attempts: 0, nextAttemptAt: null, error: null, ...overrides });
    const outbox = frozenOutbox(
      entry('e1', 2, 'pending'),
      entry('e2', 1, 'failed', { attempts: 2, nextAttemptAt: '2026-08-21T00:05:00Z', error: 'conflict' }),
      entry('e3', 3, 'uploading'),
      entry('e4', 4, 'rejected', { attempts: 3, error: 'device revoked' }),
      entry('e5', 5, 'acknowledged'),
    );
    const summary = summarize(outbox, { lastServerSequence: 41 }, 'offline');
    expect(summary).toMatchObject({ total: 5, pending: 1, uploading: 1, failed: 1, rejected: 1, acknowledged: 1, nextRetryAt: '2026-08-21T00:05:00Z', cursor: 41, idle: false });

    const allSettled = frozenOutbox(...outbox.map((item) => ({ ...item, status: 'acknowledged' as const })));
    expect(summarize(allSettled, { lastServerSequence: 43 }, 'online')).toMatchObject({ pending: 0, uploading: 0, failed: 0, rejected: 0, idle: true, nextRetryAt: null });
    expect(summarize(frozenOutbox(entry('e9', 9, 'pending')), initialCursor(), 'online').idle).toBe(false);
    expect(summarize(frozenOutbox(entry('e9', 9, 'pending')), initialCursor(), 'offline').idle).toBe(false);
  });
});

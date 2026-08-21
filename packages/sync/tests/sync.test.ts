import { describe, expect, it } from 'vitest';
import { acknowledge, enqueue, markFailed, retryDelayMs, sortRemoteChanges, type SyncEnvelope } from '../src/index';

const envelope: SyncEnvelope<{ saleId: string }> = { eventId: 'e1', idempotencyKey: 'device:1:event:1', deviceId: 'd1', organizationId: 'o1', storeId: 's1', userId: 'u1', eventType: 'sale.create', createdAt: '2026-08-21T00:00:00Z', clientSequence: 1, payload: { saleId: 'sale1' } };
describe('sync', () => {
  it('deduplicates enqueue and acknowledges only after server response', () => { const pending = enqueue([], envelope); expect(enqueue(pending, envelope)).toHaveLength(1); expect(pending[0]?.status).toBe('pending'); expect(acknowledge(pending, { eventId: 'e1', serverSequence: 8, duplicate: false })[0]?.status).toBe('acknowledged'); });
  it('preserves actionable failures and uses server ordering', () => { const failed = markFailed(enqueue([], envelope), 'e1', 'stock conflict', '2026-08-21T00:01:00Z'); expect(failed[0]).toMatchObject({ status: 'failed', attempts: 1, error: 'stock conflict' }); expect(sortRemoteChanges([{ serverSequence: 2, eventType: 'b', payload: {} }, { serverSequence: 1, eventType: 'a', payload: {} }]).map((change) => change.serverSequence)).toEqual([1, 2]); expect(retryDelayMs(10)).toBe(60000); });
});

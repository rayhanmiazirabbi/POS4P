import { describe, expect, it } from 'vitest';
import { appendTransaction, loyaltyBalance, pointsForAmount, type LoyaltyTransaction } from '../src/index';

const earn: LoyaltyTransaction = { id: 't1', accountId: 'a1', kind: 'earn', pointsDelta: 10, sourceType: 'sale', sourceId: 's1', idempotencyKey: 'sale:s1', createdAt: '2026-08-21T00:00:00Z' };
describe('loyalty', () => {
  it('calculates policy points and keeps transactions append-only', () => { expect(pointsForAmount(125, { pointsPerUnit: 1, unitAmount: 10 })).toBe(12); const ledger = appendTransaction([], earn); expect(appendTransaction(ledger, earn)).toEqual(ledger); expect(loyaltyBalance(ledger)).toBe(10); });
  it('reverses earned points with refunds', () => { expect(loyaltyBalance([earn, { ...earn, id: 't2', kind: 'refund', pointsDelta: -10, sourceType: 'return', sourceId: 'r1', idempotencyKey: 'return:r1' }])).toBe(0); });
});

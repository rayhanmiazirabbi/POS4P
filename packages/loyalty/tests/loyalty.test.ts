import { describe, expect, it } from 'vitest';
import {
  appendTransaction,
  clampRedemption,
  enrollCustomer,
  expireDuePoints,
  findActiveAccount,
  loyaltyBalance,
  loyaltySummary,
  nextExpiryAt,
  pendingExpiry,
  pointsForAmount,
  pointsForValue,
  redeemableValue,
  type LoyaltyTransaction,
} from '../src/index';

const earn: LoyaltyTransaction = { id: 't1', accountId: 'a1', kind: 'earn', pointsDelta: 10, sourceType: 'sale', sourceId: 's1', idempotencyKey: 'sale:s1', createdAt: '2026-08-21T00:00:00Z' };

function tx(overrides: Partial<LoyaltyTransaction> & { id: string; idempotencyKey: string }): LoyaltyTransaction {
  return { ...earn, ...overrides };
}

describe('loyalty', () => {
  it('calculates policy points and keeps transactions append-only', () => {
    expect(pointsForAmount(125, { pointsPerUnit: 1, unitAmount: 10 })).toBe(12);
    const ledger = appendTransaction([], earn);
    expect(appendTransaction(ledger, earn)).toEqual(ledger);
    expect(loyaltyBalance(ledger)).toBe(10);
  });

  it('reverses earned points with refunds', () => {
    expect(loyaltyBalance([earn, { ...earn, id: 't2', kind: 'refund', pointsDelta: -10, sourceType: 'return', sourceId: 'r1', idempotencyKey: 'return:r1' }])).toBe(0);
  });

  it('floors earn rounding and applies per-transaction caps', () => {
    expect(pointsForAmount(9.99, { pointsPerUnit: 1, unitAmount: 10 })).toBe(0);
    expect(pointsForAmount(20, { pointsPerUnit: 2, unitAmount: 10 })).toBe(4);
    expect(pointsForAmount(1000, { pointsPerUnit: 2, unitAmount: 100, maxPoints: 5 })).toBe(5);
    expect(() => pointsForAmount(-1, { pointsPerUnit: 1, unitAmount: 10 })).toThrow();
    expect(() => pointsForAmount(100, { pointsPerUnit: 1, unitAmount: 0 })).toThrow();
  });

  it('defines redemption rounding as floor-to-cent with capped redemptions', () => {
    const policy = { pointValue: 0.25 };
    expect(redeemableValue(10, policy)).toBe('2.50');
    expect(redeemableValue(3, { pointValue: 0.333 })).toBe('0.99');
    expect(pointsForValue(2.5, policy)).toBe(10);
    expect(pointsForValue(0.01, policy)).toBe(1);
    expect(clampRedemption(50, { pointValue: 0.25, minPointsPerRedemption: 100 })).toBe(0);
    expect(clampRedemption(9000, { pointValue: 0.25, maxPointsPerRedemption: 5000 })).toBe(5000);
    expect(clampRedemption(150, { pointValue: 0.25, minPointsPerRedemption: 100, maxPointsPerRedemption: 5000 })).toBe(150);
  });

  it('rejects malformed transactions and enforces sign invariants', () => {
    const ledger = [earn];
    expect(() => appendTransaction([], tx({ id: 't2', idempotencyKey: 'k', kind: 'redeem', pointsDelta: 5 }))).toThrow(/negative/);
    expect(() => appendTransaction([], tx({ id: 't2', idempotencyKey: 'k', kind: 'refund', pointsDelta: 5 }))).toThrow(/negative/);
    expect(() => appendTransaction([], tx({ id: 't2', idempotencyKey: 'k', kind: 'adjustment', pointsDelta: 0 }))).toThrow(/non-zero integer/);
    expect(() => appendTransaction([], tx({ id: 't2', idempotencyKey: 'k', sourceId: '' }))).toThrow(/source id/);
    expect(() => appendTransaction([], tx({ id: 't2', idempotencyKey: 'k', createdAt: 'not-a-date' }))).toThrow(/createdAt/);
    expect(appendTransaction(ledger, tx({ id: 't2', idempotencyKey: 'adj:1', kind: 'adjustment', pointsDelta: 5, sourceType: 'manual-adjustment' }))).toHaveLength(2);
  });

  it('ignores duplicate events by idempotency key during replay', () => {
    const ledger = appendTransaction([], earn);
    const replayed = appendTransaction(appendTransaction(ledger, earn), earn);
    expect(replayed).toEqual(ledger);
    expect(loyaltyBalance(replayed)).toBe(10);
  });

  it('guards concurrent redemption so replayed balances never go negative', () => {
    const ledger = appendTransaction([], earn);
    expect(() => appendTransaction(ledger, tx({ id: 't2', idempotencyKey: 'redeem:s9', kind: 'redeem', pointsDelta: -11, sourceType: 'redemption', sourceId: 's9' }))).toThrow(/Insufficient loyalty balance/);
    expect(() => loyaltySummary([tx({ id: 't2', idempotencyKey: 'x', kind: 'redeem', pointsDelta: -99 })], '2026-08-22T00:00:00Z')).toThrow(/overdrawn/);
  });

  it('expires earned points FIFO while spending never-expiring lots last', () => {
    const january = tx({ id: 'e1', idempotencyKey: 'sale:e1', pointsDelta: 100, createdAt: '2026-01-01T00:00:00Z', expiresAt: '2026-02-01T00:00:00Z' });
    const protectedLot = tx({ id: 'e2', idempotencyKey: 'sale:e2', pointsDelta: 50, createdAt: '2026-01-15T00:00:00Z' });
    const redemption = tx({ id: 'r1', idempotencyKey: 'redeem:r1', kind: 'redeem', pointsDelta: -60, sourceType: 'redemption', sourceId: 'ord:1', createdAt: '2026-01-20T00:00:00Z' });
    const ledger = [january, protectedLot, redemption];
    expect(loyaltyBalance(ledger)).toBe(90);
    expect(pendingExpiry(ledger, '2026-02-01T00:00:00Z')).toBe(40);
    expect(nextExpiryAt(ledger, '2026-01-31T00:00:00Z')).toBe('2026-02-01T00:00:00Z');
    const expired = expireDuePoints(ledger, { accountId: 'a1', id: 'x1', sourceId: 'job:1', idempotencyKey: 'expire:a1:2026-02', createdAt: '2026-02-01T00:00:00Z' }, '2026-02-01T00:00:00Z');
    expect(expired.expiredPoints).toBe(40);
    expect(loyaltyBalance(expired.transactions)).toBe(50);
    const replayed = expireDuePoints(expired.transactions, { accountId: 'a1', id: 'x1', sourceId: 'job:1', idempotencyKey: 'expire:a1:2026-02', createdAt: '2026-02-01T00:00:00Z' }, '2026-02-01T00:00:00Z');
    expect(replayed).toEqual({ transactions: expired.transactions, expiredPoints: 0 });
    expect(pendingExpiry(expired.transactions, '2027-01-01T00:00:00Z')).toBe(0);
  });

  it('surfaces upcoming expiries after the cutoff', () => {
    const soon = tx({ id: 'e1', idempotencyKey: 'sale:e1', pointsDelta: 30, createdAt: '2026-06-01T00:00:00Z', expiresAt: '2026-09-01T00:00:00Z' });
    const later = tx({ id: 'e2', idempotencyKey: 'sale:e2', pointsDelta: 70, createdAt: '2026-05-01T00:00:00Z', expiresAt: '2026-12-01T00:00:00Z' });
    expect(nextExpiryAt([later, soon], '2026-08-01T00:00:00Z')).toBe('2026-09-01T00:00:00Z');
  });

  it('keeps earned points valid across policy changes', () => {
    const oldPolicyLedger = appendTransaction([], tx({ id: 'e1', idempotencyKey: 'sale:e1', pointsDelta: pointsForAmount(120, { pointsPerUnit: 1, unitAmount: 10 }), createdAt: '2026-07-01T00:00:00Z' }));
    expect(pointsForAmount(120, { pointsPerUnit: 1, unitAmount: 50, maxPoints: 1 })).toBe(1);
    const redeemed = appendTransaction(oldPolicyLedger, tx({ id: 'r1', idempotencyKey: 'redeem:r1', kind: 'redeem', pointsDelta: -clampRedemption(12, { pointValue: 0.25, maxPointsPerRedemption: 5000 }), sourceType: 'redemption', sourceId: 'ord:9' }));
    expect(loyaltyBalance(redeemed)).toBe(0);
  });

  it('enrolls one active account per organization and customer', () => {
    const account = { id: 'acc1', organizationId: 'o1', customerId: 'c1', status: 'active' as const, enrolledAt: '2026-08-21T00:00:00Z' };
    const accounts = enrollCustomer([], account);
    expect(findActiveAccount(accounts, 'o1', 'c1')).toEqual(account);
    expect(findActiveAccount(accounts, 'o2', 'c1')).toBeUndefined();
    expect(() => enrollCustomer(accounts, { ...account, id: 'acc2' })).toThrow(/already has an active loyalty account/);
    const closedOnly = [{ ...account, id: 'acc9', status: 'closed' as const }];
    expect(enrollCustomer(closedOnly, account)).toHaveLength(2);
  });

  it('rebuilds summaries from the ledger identically to incremental folds', () => {
    const incremental = [
      tx({ id: 'e1', idempotencyKey: 'sale:e1', pointsDelta: 100, createdAt: '2026-05-01T00:00:00Z', expiresAt: '2027-05-01T00:00:00Z' }),
      tx({ id: 'r1', idempotencyKey: 'redeem:r1', kind: 'redeem', pointsDelta: -30, sourceType: 'redemption', sourceId: 'ord:1', createdAt: '2026-06-01T00:00:00Z' }),
      tx({ id: 'f1', idempotencyKey: 'return:f1', kind: 'refund', pointsDelta: -5, sourceType: 'return', sourceId: 'ret:1', createdAt: '2026-06-02T00:00:00Z' }),
    ];
    let folded: LoyaltyTransaction[] = [];
    for (const transaction of incremental) folded = appendTransaction(folded, transaction);
    expect(folded.map((transaction) => transaction.id)).toEqual(['e1', 'r1', 'f1']);
    const asOf = '2026-08-22T00:00:00Z';
    expect(loyaltySummary(incremental, asOf)).toEqual({
      balance: 65,
      lifetimeEarned: 100,
      lifetimeRedeemed: 30,
      lifetimeRefunded: 5,
      lifetimeExpired: 0,
      pendingExpiry: 0,
      nextExpiryAt: '2027-05-01T00:00:00Z',
    });
    expect(loyaltySummary(folded, asOf)).toEqual(loyaltySummary(incremental, asOf));
  });
});

export type LoyaltyTransactionKind = 'earn' | 'redeem' | 'refund' | 'expire' | 'adjustment';
export type LoyaltyAccountStatus = 'active' | 'closed';

export type LoyaltyAccount = { id: string; organizationId: string; customerId: string; status: LoyaltyAccountStatus; enrolledAt: string };

export type LoyaltyTransaction = {
  id: string;
  accountId: string;
  kind: LoyaltyTransactionKind;
  pointsDelta: number;
  sourceType: string;
  sourceId: string;
  idempotencyKey: string;
  createdAt: string;
  expiresAt?: string;
};

export type EarnPolicy = { pointsPerUnit: number; unitAmount: number; maxPoints?: number };
export type RedeemPolicy = { pointValue: number; minPointsPerRedemption?: number; maxPointsPerRedemption?: number };

export type LoyaltySummary = {
  balance: number;
  lifetimeEarned: number;
  lifetimeRedeemed: number;
  lifetimeRefunded: number;
  lifetimeExpired: number;
  pendingExpiry: number;
  nextExpiryAt: string | null;
};

const REQUIRED_DELTA_SIGN: Record<LoyaltyTransactionKind, number> = { earn: 1, redeem: -1, refund: -1, expire: -1, adjustment: 0 };

function toCents(amount: number): number {
  if (!Number.isFinite(amount)) throw new Error('Invalid amount');
  return Math.round(amount * 100);
}

function formatCents(cents: number): string {
  const sign = cents < 0 ? '-' : '';
  const absolute = Math.abs(cents);
  return `${sign}${Math.floor(absolute / 100)}.${(absolute % 100).toString().padStart(2, '0')}`;
}

function assertNonEmptyString(value: unknown, field: string): void {
  if (typeof value !== 'string' || value.trim() === '') throw new Error(`Loyalty ${field} is required`);
}

function assertIsoDateTime(value: unknown, field: string): void {
  if (typeof value !== 'string' || Number.isNaN(Date.parse(value))) throw new Error(`Loyalty ${field} must be a valid ISO timestamp`);
}

function assertEarnPolicy(policy: EarnPolicy): void {
  if (!Number.isInteger(policy.pointsPerUnit) || policy.pointsPerUnit < 0) throw new Error('Invalid loyalty policy');
  if (!Number.isFinite(policy.unitAmount) || toCents(policy.unitAmount) <= 0) throw new Error('Invalid loyalty policy');
  if (policy.maxPoints !== undefined && (!Number.isInteger(policy.maxPoints) || policy.maxPoints < 0)) throw new Error('Invalid loyalty policy');
}

function assertRedeemPolicy(policy: RedeemPolicy): void {
  if (!Number.isFinite(policy.pointValue) || toCents(policy.pointValue) <= 0) throw new Error('Invalid redemption policy');
  const min = policy.minPointsPerRedemption ?? 0;
  const max = policy.maxPointsPerRedemption ?? Number.MAX_SAFE_INTEGER;
  if (!Number.isInteger(min) || min < 0 || !Number.isInteger(max) || max < min) throw new Error('Invalid redemption policy');
}

export function pointsForAmount(amount: number, policy: EarnPolicy): number {
  assertEarnPolicy(policy);
  if (amount < 0) throw new Error('Sale amount must not be negative');
  const earned = Math.floor(toCents(amount) / toCents(policy.unitAmount)) * policy.pointsPerUnit;
  return Math.min(policy.maxPoints ?? Number.MAX_SAFE_INTEGER, earned);
}

export function redeemableValue(points: number, policy: RedeemPolicy): string {
  assertRedeemPolicy(policy);
  if (!Number.isInteger(points) || points < 0) throw new Error('Points must be a non-negative integer');
  return formatCents(Math.floor(points * toCents(policy.pointValue)));
}

export function pointsForValue(amount: number, policy: RedeemPolicy): number {
  assertRedeemPolicy(policy);
  if (amount < 0) throw new Error('Redemption amount must not be negative');
  return Math.ceil(toCents(amount) / toCents(policy.pointValue));
}

export function clampRedemption(points: number, policy: RedeemPolicy): number {
  assertRedeemPolicy(policy);
  if (!Number.isInteger(points) || points < 0) throw new Error('Points must be a non-negative integer');
  if (points < (policy.minPointsPerRedemption ?? 0)) return 0;
  return Math.min(points, policy.maxPointsPerRedemption ?? Number.MAX_SAFE_INTEGER);
}

export function loyaltyBalance(transactions: readonly LoyaltyTransaction[]): number {
  return transactions.reduce((balance, transaction) => balance + transaction.pointsDelta, 0);
}

function assertValidTransaction(transaction: LoyaltyTransaction): void {
  assertNonEmptyString(transaction.id, 'transaction id');
  assertNonEmptyString(transaction.accountId, 'account id');
  assertNonEmptyString(transaction.sourceType, 'source type');
  assertNonEmptyString(transaction.sourceId, 'source id');
  assertNonEmptyString(transaction.idempotencyKey, 'idempotency key');
  assertIsoDateTime(transaction.createdAt, 'createdAt');
  if (transaction.expiresAt !== undefined) assertIsoDateTime(transaction.expiresAt, 'expiresAt');
  if (!Number.isInteger(transaction.pointsDelta) || transaction.pointsDelta === 0) throw new Error('Points delta must be a non-zero integer');
  const requiredSign = REQUIRED_DELTA_SIGN[transaction.kind];
  if (requiredSign !== 0 && Math.sign(transaction.pointsDelta) !== requiredSign) {
    throw new Error(`Loyalty ${transaction.kind} points delta must be ${requiredSign > 0 ? 'positive' : 'negative'}`);
  }
  if (transaction.kind === 'expire' && transaction.expiresAt !== undefined) throw new Error('Expire transactions must not carry an expiry');
}

export function appendTransaction(transactions: readonly LoyaltyTransaction[], transaction: LoyaltyTransaction): LoyaltyTransaction[] {
  if (transactions.some((item) => item.idempotencyKey === transaction.idempotencyKey)) return [...transactions];
  assertValidTransaction(transaction);
  if (loyaltyBalance(transactions) + transaction.pointsDelta < 0) throw new Error('Insufficient loyalty balance');
  return [...transactions, { ...transaction }];
}

type PointLot = { remaining: number; expiresAt: string | null };

function byEarliestExpiry(a: PointLot, b: PointLot): number {
  const left = a.expiresAt ? Date.parse(a.expiresAt) : Number.POSITIVE_INFINITY;
  const right = b.expiresAt ? Date.parse(b.expiresAt) : Number.POSITIVE_INFINITY;
  return left - right;
}

function projectLots(transactions: readonly LoyaltyTransaction[]): PointLot[] {
  const ordered = [...transactions].sort((a, b) => Date.parse(a.createdAt) - Date.parse(b.createdAt));
  const lots: PointLot[] = [];
  for (const transaction of ordered) {
    if (transaction.pointsDelta > 0) {
      lots.push({ remaining: transaction.pointsDelta, expiresAt: transaction.kind === 'earn' ? transaction.expiresAt ?? null : null });
      continue;
    }
    let remaining = -transaction.pointsDelta;
    const consumable = lots.filter((lot) => lot.remaining > 0).sort(byEarliestExpiry);
    for (const lot of consumable) {
      if (remaining === 0) break;
      const taken = Math.min(lot.remaining, remaining);
      lot.remaining -= taken;
      remaining -= taken;
    }
    if (remaining > 0) throw new Error('Loyalty ledger overdrawn');
  }
  return lots;
}

function parseAsOf(asOf: string): number {
  assertIsoDateTime(asOf, 'asOf');
  return Date.parse(asOf);
}

export function pendingExpiry(transactions: readonly LoyaltyTransaction[], asOf: string): number {
  const cutoff = parseAsOf(asOf);
  return projectLots(transactions)
    .filter((lot) => lot.expiresAt !== null && Date.parse(lot.expiresAt) <= cutoff)
    .reduce((total, lot) => total + lot.remaining, 0);
}

export function nextExpiryAt(transactions: readonly LoyaltyTransaction[], asOf: string): string | null {
  const cutoff = parseAsOf(asOf);
  const upcoming = projectLots(transactions)
    .filter((lot) => lot.remaining > 0 && lot.expiresAt !== null && Date.parse(lot.expiresAt) > cutoff)
    .map((lot) => lot.expiresAt as string)
    .sort();
  return upcoming[0] ?? null;
}

export function expireDuePoints(
  transactions: readonly LoyaltyTransaction[],
  reference: { accountId: string; id: string; sourceId: string; idempotencyKey: string; createdAt: string },
  asOf: string,
): { transactions: LoyaltyTransaction[]; expiredPoints: number } {
  const due = pendingExpiry(
    transactions.filter((transaction) => transaction.accountId === reference.accountId),
    asOf,
  );
  if (due === 0) return { transactions: [...transactions], expiredPoints: 0 };
  return {
    transactions: appendTransaction(transactions, {
      id: reference.id,
      accountId: reference.accountId,
      kind: 'expire',
      pointsDelta: -due,
      sourceType: 'expiry-job',
      sourceId: reference.sourceId,
      idempotencyKey: reference.idempotencyKey,
      createdAt: reference.createdAt,
    }),
    expiredPoints: due,
  };
}

export function loyaltySummary(transactions: readonly LoyaltyTransaction[], asOf: string = new Date().toISOString()): LoyaltySummary {
  const sumKind = (kind: LoyaltyTransactionKind): number =>
    transactions.filter((transaction) => transaction.kind === kind).reduce((total, transaction) => total + Math.abs(transaction.pointsDelta), 0);
  return {
    balance: loyaltyBalance(transactions),
    lifetimeEarned: sumKind('earn'),
    lifetimeRedeemed: sumKind('redeem'),
    lifetimeRefunded: sumKind('refund'),
    lifetimeExpired: sumKind('expire'),
    pendingExpiry: pendingExpiry(transactions, asOf),
    nextExpiryAt: nextExpiryAt(transactions, asOf),
  };
}

export function findActiveAccount(accounts: readonly LoyaltyAccount[], organizationId: string, customerId: string): LoyaltyAccount | undefined {
  return accounts.find((account) => account.organizationId === organizationId && account.customerId === customerId && account.status === 'active');
}

export function enrollCustomer(accounts: readonly LoyaltyAccount[], account: LoyaltyAccount): LoyaltyAccount[] {
  assertNonEmptyString(account.id, 'account id');
  assertNonEmptyString(account.organizationId, 'organization id');
  assertNonEmptyString(account.customerId, 'customer id');
  assertIsoDateTime(account.enrolledAt, 'enrolledAt');
  if (account.status !== 'active' && account.status !== 'closed') throw new Error('Loyalty account status is invalid');
  if (findActiveAccount(accounts, account.organizationId, account.customerId)) throw new Error('Customer already has an active loyalty account');
  return [...accounts, { ...account }];
}

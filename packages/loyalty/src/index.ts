export type LoyaltyTransactionKind = 'earn' | 'redeem' | 'refund' | 'expire' | 'adjustment';
export type LoyaltyTransaction = { id: string; accountId: string; kind: LoyaltyTransactionKind; pointsDelta: number; sourceType: string; sourceId: string; idempotencyKey: string; createdAt: string };
export type EarnPolicy = { pointsPerUnit: number; unitAmount: number; maxPoints?: number };

export function pointsForAmount(amount: number, policy: EarnPolicy): number { if (amount < 0 || policy.unitAmount <= 0 || policy.pointsPerUnit < 0) throw new Error('Invalid loyalty policy'); return Math.min(policy.maxPoints ?? Number.MAX_SAFE_INTEGER, Math.floor(amount / policy.unitAmount) * policy.pointsPerUnit); }
export function loyaltyBalance(transactions: readonly LoyaltyTransaction[]): number { return transactions.reduce((balance, transaction) => balance + transaction.pointsDelta, 0); }
export function appendTransaction(transactions: readonly LoyaltyTransaction[], transaction: LoyaltyTransaction): LoyaltyTransaction[] { if (transactions.some((item) => item.idempotencyKey === transaction.idempotencyKey)) return [...transactions]; if (!Number.isInteger(transaction.pointsDelta) || transaction.pointsDelta === 0) throw new Error('Points delta must be a non-zero integer'); return [...transactions, { ...transaction }]; }

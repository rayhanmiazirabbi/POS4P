export type SyncEnvelope<T = unknown> = { eventId: string; idempotencyKey: string; deviceId: string; organizationId: string; storeId: string; userId: string; eventType: string; createdAt: string; clientSequence: number; payload: T };
export type OutboxStatus = 'pending' | 'uploading' | 'acknowledged' | 'failed';
export type OutboxEntry<T = unknown> = { envelope: SyncEnvelope<T>; status: OutboxStatus; attempts: number; nextAttemptAt: string | null; error: string | null };
export type SyncAcknowledgement = { eventId: string; serverSequence: number; duplicate: boolean };
export type RemoteChange<T = unknown> = { serverSequence: number; eventType: string; payload: T };
export type PullPage<T = unknown> = { changes: readonly RemoteChange<T>[]; nextCursor: string; hasMore: boolean };

export function enqueue<T>(outbox: readonly OutboxEntry[], envelope: SyncEnvelope<T>): OutboxEntry[] { if (outbox.some((entry) => entry.envelope.eventId === envelope.eventId || entry.envelope.idempotencyKey === envelope.idempotencyKey)) return [...outbox]; if (!Number.isInteger(envelope.clientSequence) || envelope.clientSequence < 1) throw new Error('Invalid client sequence'); return [...outbox, { envelope, status: 'pending', attempts: 0, nextAttemptAt: null, error: null }]; }
export function acknowledge(outbox: readonly OutboxEntry[], acknowledgement: SyncAcknowledgement): OutboxEntry[] { return outbox.map((entry) => entry.envelope.eventId === acknowledgement.eventId ? { ...entry, status: 'acknowledged', error: null, nextAttemptAt: null } : entry); }
export function markFailed(outbox: readonly OutboxEntry[], eventId: string, reason: string, nextAttemptAt: string): OutboxEntry[] { return outbox.map((entry) => entry.envelope.eventId === eventId ? { ...entry, status: 'failed', attempts: entry.attempts + 1, error: reason, nextAttemptAt } : entry); }
export function sortRemoteChanges<T>(changes: readonly RemoteChange<T>[]): RemoteChange<T>[] { return [...changes].sort((a, b) => a.serverSequence - b.serverSequence); }
export function retryDelayMs(attempt: number, baseMs = 1000, maxMs = 60_000): number { if (!Number.isInteger(attempt) || attempt < 0) throw new Error('Invalid retry attempt'); return Math.min(maxMs, baseMs * (2 ** attempt)); }

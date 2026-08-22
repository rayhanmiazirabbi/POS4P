import { createId } from '@pharmacy/core';

import { ApiRequestError, decodeApiError, isRetryableErrorCode, isRetryableStatus } from './errors';

/**
 * Methods that may be replayed without an idempotency key.
 *
 * `PUT` and `DELETE` are idempotent by definition (RFC 9110 §9.2.2); `POST` and
 * `PATCH` are not, so replaying either without a key risks a duplicate sale.
 */
const idempotentMethods: readonly string[] = ['GET', 'HEAD', 'OPTIONS', 'PUT', 'DELETE'];

/** Methods that carry no body and only read state. */
const safeMethods: readonly string[] = ['GET', 'HEAD', 'OPTIONS'];

export function methodOf(init: RequestInit | undefined): string {
  return (init?.method ?? 'GET').toUpperCase();
}

export function isSafeMethod(method: string): boolean {
  return safeMethods.includes(method.toUpperCase());
}

export function isIdempotentMethod(method: string): boolean {
  return idempotentMethods.includes(method.toUpperCase());
}

/** The backend accepts an `Idempotency-Key` of 16-128 characters. */
export const idempotencyKeyMinLength = 16;
export const idempotencyKeyMaxLength = 128;

export function isValidIdempotencyKey(key: string): boolean {
  const trimmed = key.trim();
  return trimmed.length >= idempotencyKeyMinLength && trimmed.length <= idempotencyKeyMaxLength;
}

/**
 * Mint a key the backend will accept. UUIDv7 is 36 characters and monotonic, so
 * retries of the same logical operation keep their original ordering.
 */
export function createIdempotencyKey(prefix?: string): string {
  const id = createId();
  const key = prefix ? `${prefix}-${id}` : id;
  return key.slice(0, idempotencyKeyMaxLength);
}

export function assertIdempotencyKey(key: string): string {
  const trimmed = key.trim();
  if (!isValidIdempotencyKey(trimmed)) {
    throw new ApiRequestError({
      code: 'VALIDATION_ERROR',
      message: `Idempotency-Key must be between ${idempotencyKeyMinLength} and ${idempotencyKeyMaxLength} characters`,
      requestId: 'unknown',
    });
  }
  return trimmed;
}

export type RetryPolicy = {
  /** Total attempts including the first. `1` disables retries. */
  attempts: number;
  /** Backoff base; delay for attempt `n` is `baseDelayMs * 2 ** (n - 1)`. */
  baseDelayMs: number;
  maxDelayMs: number;
  /** Fraction of the delay randomised, `0` for deterministic backoff. */
  jitterRatio: number;
};

export const defaultRetryPolicy: RetryPolicy = { attempts: 3, baseDelayMs: 200, maxDelayMs: 5_000, jitterRatio: 0 };

/** Wall-clock budget for a single attempt, applied by the client's own timer. */
export const defaultTimeoutMs = 15_000;

export function retryDelayMs(attempt: number, policy: RetryPolicy = defaultRetryPolicy, random: () => number = Math.random): number {
  if (!Number.isInteger(attempt) || attempt < 1) throw new Error('Retry attempt must be a positive integer');
  const exponential = Math.min(policy.maxDelayMs, policy.baseDelayMs * 2 ** (attempt - 1));
  if (policy.jitterRatio <= 0) return exponential;
  const spread = exponential * Math.min(1, policy.jitterRatio);
  return Math.round(exponential - spread + random() * spread * 2);
}

export type RetryDecision = { retry: boolean; reason: string };

export type RetryContext = {
  method: string;
  /** Whether an `Idempotency-Key` was sent with the request. */
  hasIdempotencyKey: boolean;
  attempt: number;
  policy: RetryPolicy;
  error: unknown;
};

/**
 * The one rule this layer must never break: a non-idempotent request without an
 * idempotency key is never replayed, however transient the failure looks. A
 * timed-out `POST /sales` may well have committed on the server.
 */
export function shouldRetry(context: RetryContext): RetryDecision {
  if (context.attempt >= context.policy.attempts) return { retry: false, reason: 'attempts-exhausted' };
  if (!isIdempotentMethod(context.method) && !context.hasIdempotencyKey) {
    return { retry: false, reason: 'non-idempotent-without-key' };
  }
  const decoded = decodeApiError(context.error);
  if (decoded.code === 'ABORTED') return { retry: false, reason: 'aborted' };
  const status = context.error instanceof ApiRequestError ? context.error.status : null;
  if (isRetryableErrorCode(decoded.code)) return { retry: true, reason: `retryable-code:${decoded.code}` };
  if (status !== null && isRetryableStatus(status)) return { retry: true, reason: `retryable-status:${status}` };
  return { retry: false, reason: `terminal-code:${decoded.code}` };
}

export type Sleep = (ms: number) => Promise<void>;

export const defaultSleep: Sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

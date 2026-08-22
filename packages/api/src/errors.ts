import type { ApiError } from '@pharmacy/types';

/**
 * Error codes the server may send, mirroring `ERROR_STATUS` in
 * `backend/app/errors.py`. Codes are preserved verbatim by `decodeApiError`;
 * this list only drives retry and status mapping decisions.
 */
export const serverErrorCodes = [
  'VALIDATION_ERROR',
  'UNAUTHORIZED',
  'FORBIDDEN',
  'NOT_FOUND',
  'CONFLICT',
  'IDEMPOTENCY_CONFLICT',
  'INSUFFICIENT_STOCK',
  'RATE_LIMITED',
  'STORE_CONTEXT_REQUIRED',
  'INTERNAL_ERROR',
] as const;

export type ServerErrorCode = (typeof serverErrorCodes)[number];

/** HTTP status per server error code, mirroring `ERROR_STATUS` in the backend. */
export const errorStatus: Readonly<Record<ServerErrorCode, number>> = {
  VALIDATION_ERROR: 422,
  UNAUTHORIZED: 401,
  FORBIDDEN: 403,
  NOT_FOUND: 404,
  CONFLICT: 409,
  IDEMPOTENCY_CONFLICT: 409,
  INSUFFICIENT_STOCK: 409,
  RATE_LIMITED: 429,
  STORE_CONTEXT_REQUIRED: 400,
  INTERNAL_ERROR: 500,
};

/**
 * Codes produced on the client only; the server never sends them, so they can
 * never collide with a preserved server code.
 */
export const clientErrorCodes = ['TIMEOUT', 'NETWORK_ERROR', 'ABORTED'] as const;
export type ClientErrorCode = (typeof clientErrorCodes)[number];

export type ApiErrorCode = ServerErrorCode | ClientErrorCode;

export function isServerErrorCode(code: string): code is ServerErrorCode {
  return (serverErrorCodes as readonly string[]).includes(code);
}

/** Status a given code maps to, or `null` for client-only codes. */
export function statusForErrorCode(code: string): number | null {
  return isServerErrorCode(code) ? errorStatus[code] : null;
}

/**
 * Codes worth retrying: transient server or transport conditions only. A
 * `CONFLICT`, `IDEMPOTENCY_CONFLICT`, or `VALIDATION_ERROR` is a decision the
 * server already made, so replaying it would only duplicate work.
 */
const retryableCodes: readonly string[] = ['RATE_LIMITED', 'INTERNAL_ERROR', 'TIMEOUT', 'NETWORK_ERROR'];

export function isRetryableErrorCode(code: string): boolean {
  return retryableCodes.includes(code);
}

/** Statuses worth retrying when only a status is known (no decoded body). */
export function isRetryableStatus(status: number): boolean {
  return status === 429 || status === 408 || (status >= 500 && status <= 599);
}

/**
 * Transport-level failure carrying the decoded, code-preserving body.
 *
 * Transports throw this so `ApiClient` can make retry decisions without
 * inspecting HTTP plumbing, and callers can read `error.code` verbatim.
 */
export class ApiRequestError extends Error {
  readonly error: ApiError;
  readonly status: number | null;
  readonly retryable: boolean;

  constructor(error: ApiError, status: number | null = null) {
    super(error.message);
    this.name = 'ApiRequestError';
    this.error = error;
    this.status = status ?? statusForErrorCode(error.code);
    this.retryable = isRetryableErrorCode(error.code);
  }

  get code(): string {
    return this.error.code;
  }

  get requestId(): string {
    return this.error.requestId;
  }

  get fieldErrors(): Record<string, string[]> | undefined {
    return this.error.fieldErrors;
  }
}

export function isApiRequestError(value: unknown): value is ApiRequestError {
  return value instanceof ApiRequestError;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null;
}

/** Accept `fieldErrors` only in the exact wire shape; drop anything else. */
function decodeFieldErrors(value: unknown): Record<string, string[]> | undefined {
  if (!isRecord(value)) return undefined;
  const decoded: Record<string, string[]> = {};
  for (const [field, messages] of Object.entries(value)) {
    if (!Array.isArray(messages)) continue;
    const texts = messages.filter((message): message is string => typeof message === 'string');
    if (texts.length > 0) decoded[field] = texts;
  }
  return Object.keys(decoded).length > 0 ? decoded : undefined;
}

function abortCode(value: object): ClientErrorCode | null {
  const name = 'name' in value && typeof value.name === 'string' ? value.name : '';
  if (name === 'TimeoutError') return 'TIMEOUT';
  if (name === 'AbortError') return 'ABORTED';
  return null;
}

/**
 * Normalize anything a transport can fail with into an `ApiError`.
 *
 * Server codes survive verbatim; only malformed or missing fields fall back, so
 * a garbled 500 body still yields a usable error instead of throwing again.
 */
export function decodeApiError(error: unknown): ApiError {
  if (error instanceof ApiRequestError) return error.error;
  if (typeof error === 'string') {
    try {
      return decodeApiError(JSON.parse(error) as unknown);
    } catch {
      return { code: 'INTERNAL_ERROR', message: error.trim() || 'Request failed', requestId: 'unknown' };
    }
  }
  if (typeof error === 'object' && error !== null && 'code' in error && 'message' in error) {
    const value = error as Partial<ApiError>;
    const code = typeof value.code === 'string' && value.code.trim() !== '' ? value.code : 'INTERNAL_ERROR';
    const message = typeof value.message === 'string' && value.message.trim() !== '' ? value.message : 'Request failed';
    const requestId = typeof value.requestId === 'string' && value.requestId.trim() !== '' ? value.requestId : 'unknown';
    const decoded: ApiError = { code, message, requestId };
    const fieldErrors = decodeFieldErrors(value.fieldErrors);
    if (fieldErrors) decoded.fieldErrors = fieldErrors;
    return decoded;
  }
  if (isRecord(error)) {
    const aborted = abortCode(error);
    const message = 'message' in error && typeof error.message === 'string' && error.message.trim() !== '' ? error.message : 'Request failed';
    if (aborted) return { code: aborted, message, requestId: 'unknown' };
    if (error instanceof Error) return { code: 'INTERNAL_ERROR', message, requestId: 'unknown' };
  }
  return { code: 'INTERNAL_ERROR', message: 'Request failed', requestId: 'unknown' };
}

/** Wrap any failure as an `ApiRequestError` without losing an existing one. */
export function toApiRequestError(error: unknown, status: number | null = null): ApiRequestError {
  return error instanceof ApiRequestError ? error : new ApiRequestError(decodeApiError(error), status);
}

export function timeoutError(timeoutMs: number): ApiRequestError {
  return new ApiRequestError({ code: 'TIMEOUT', message: `Request timed out after ${timeoutMs}ms`, requestId: 'unknown' }, null);
}

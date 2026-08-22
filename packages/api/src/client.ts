import type { ApiError, ApiResponse, Pagination } from '@pharmacy/types';

import { ApiRequestError, decodeApiError, timeoutError, toApiRequestError } from './errors';
import { clampLimit, decodePage, paginationQuery, type Page } from './pagination';
import {
  assertIdempotencyKey,
  createIdempotencyKey,
  defaultRetryPolicy,
  defaultSleep,
  defaultTimeoutMs,
  isIdempotentMethod,
  isSafeMethod,
  methodOf,
  retryDelayMs,
  shouldRetry,
  type RetryPolicy,
  type Sleep,
} from './policy';
import { storageKeys, type StorageAdapter } from './storage';

/** Values accepted in a query string; arrays repeat the key. */
export type QueryValue = string | number | boolean | null | undefined | readonly (string | number | boolean)[];

export type RequestOptions = {
  signal?: AbortSignal;
  idempotencyKey?: string;
  pagination?: Pagination;
  /** Extra query parameters, merged after pagination. */
  query?: Readonly<Record<string, QueryValue>>;
  /** Per-request override of the client's timeout. `0` disables it. */
  timeoutMs?: number;
  /** Per-request override of the retry policy. */
  retry?: Partial<RetryPolicy>;
  /** Body to JSON-encode. Ignored when `init.body` is already set. */
  json?: unknown;
  /** Correlates client logs with the server's `requestId`. */
  requestId?: string;
  /** Send no `Authorization` header (login and other pre-session calls). */
  anonymous?: boolean;
  /** Extra headers, applied last. */
  headers?: Readonly<Record<string, string>>;
};

/**
 * Platform HTTP boundary. Implementations must throw `ApiRequestError` (or
 * anything `decodeApiError` understands) on failure so retry decisions stay in
 * this package rather than in `fetch` glue.
 */
export type ApiTransport = <T>(path: string, init: RequestInit) => Promise<ApiResponse<T>>;

export type ApiClientConfig = {
  /** Prefixed to every path. Trailing slashes are trimmed. */
  baseUrl?: string;
  timeoutMs?: number;
  retry?: Partial<RetryPolicy>;
  /** Injected for deterministic backoff in tests. */
  sleep?: Sleep;
  defaultHeaders?: Readonly<Record<string, string>>;
  /** Observability hook. Must not make business decisions or notify the UI. */
  onRetry?: (event: RetryEvent) => void;
};

export type RetryEvent = { path: string; method: string; attempt: number; delayMs: number; reason: string; error: ApiError };

function buildQuery(options: RequestOptions): string {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(paginationQuery(options.pagination))) params.set(key, value);
  for (const [key, value] of Object.entries(options.query ?? {})) {
    if (value === null || value === undefined) continue;
    if (Array.isArray(value)) {
      for (const entry of value) params.append(key, String(entry));
      continue;
    }
    params.set(key, String(value));
  }
  const query = params.toString();
  return query === '' ? '' : `?${query}`;
}

function resolvePolicy(base: RetryPolicy, override: Partial<RetryPolicy> | undefined): RetryPolicy {
  return override === undefined ? base : { ...base, ...override };
}

/**
 * Typed transport for the FastAPI backend: auth headers, idempotency, timeout,
 * retry, structured error decoding, and pagination.
 *
 * Deliberately contains no business rules and raises no UI notifications; it
 * hands callers a decoded `ApiError` and lets feature packages decide.
 */
export class ApiClient {
  private readonly baseUrl: string;
  private readonly timeoutMs: number;
  private readonly retryPolicy: RetryPolicy;
  private readonly sleep: Sleep;
  private readonly defaultHeaders: Readonly<Record<string, string>>;
  private readonly onRetry: ((event: RetryEvent) => void) | undefined;

  constructor(
    private readonly transport: ApiTransport,
    private readonly storage?: StorageAdapter,
    config: ApiClientConfig = {},
  ) {
    this.baseUrl = (config.baseUrl ?? '').replace(/\/+$/, '');
    this.timeoutMs = config.timeoutMs ?? defaultTimeoutMs;
    this.retryPolicy = resolvePolicy(defaultRetryPolicy, config.retry);
    this.sleep = config.sleep ?? defaultSleep;
    this.defaultHeaders = config.defaultHeaders ?? {};
    this.onRetry = config.onRetry;
  }

  /**
   * Perform a request, retrying only when it is safe to do so.
   *
   * The idempotency key is resolved once and reused across attempts, so the
   * server can recognise a replay of the same logical operation.
   */
  async request<T>(path: string, init: RequestInit = {}, options: RequestOptions = {}): Promise<ApiResponse<T>> {
    const method = methodOf(init);
    const idempotencyKey = options.idempotencyKey === undefined ? undefined : assertIdempotencyKey(options.idempotencyKey);
    const headers = new Headers(init.headers);
    for (const [key, value] of Object.entries(this.defaultHeaders)) if (!headers.has(key)) headers.set(key, value);

    if (options.anonymous !== true) {
      const accessToken = await this.storage?.get(storageKeys.accessToken);
      if (accessToken) headers.set('Authorization', `Bearer ${accessToken}`);
    }
    if (idempotencyKey) headers.set('Idempotency-Key', idempotencyKey);
    if (options.requestId) headers.set('X-Request-ID', options.requestId);

    const requestInit: RequestInit = { ...init, headers };
    if (init.body === undefined && options.json !== undefined) {
      requestInit.body = JSON.stringify(options.json);
      if (!headers.has('Content-Type')) headers.set('Content-Type', 'application/json');
    }
    if (!headers.has('Accept')) headers.set('Accept', 'application/json');
    for (const [key, value] of Object.entries(options.headers ?? {})) headers.set(key, value);

    const target = `${this.baseUrl}${path}${buildQuery(options)}`;
    const policy = resolvePolicy(this.retryPolicy, options.retry);
    const timeoutMs = options.timeoutMs ?? this.timeoutMs;
    const hasIdempotencyKey = idempotencyKey !== undefined || isSafeMethod(method);

    let attempt = 0;
    for (;;) {
      attempt += 1;
      try {
        return await this.attempt<T>(target, requestInit, timeoutMs, options.signal);
      } catch (error) {
        const decision = shouldRetry({ method, hasIdempotencyKey, attempt, policy, error });
        if (!decision.retry) throw toApiRequestError(error);
        const delayMs = retryDelayMs(attempt, policy);
        this.onRetry?.({ path: target, method, attempt, delayMs, reason: decision.reason, error: decodeApiError(error) });
        await this.sleep(delayMs);
      }
    }
  }

  /**
   * One attempt under a wall-clock budget.
   *
   * The timeout races the transport and aborts it, so a stalled socket cannot
   * hold a cashier's screen; the caller's own signal is honoured too.
   */
  private async attempt<T>(path: string, init: RequestInit, timeoutMs: number, external: AbortSignal | undefined): Promise<ApiResponse<T>> {
    if (external?.aborted === true) {
      throw new ApiRequestError({ code: 'ABORTED', message: 'Request aborted by caller', requestId: 'unknown' });
    }
    const controller = new AbortController();
    const forwardAbort = (): void => controller.abort();
    external?.addEventListener('abort', forwardAbort);
    let timer: ReturnType<typeof setTimeout> | undefined;
    const attemptInit: RequestInit = { ...init, signal: controller.signal };
    try {
      if (timeoutMs <= 0) return await this.transport<T>(path, attemptInit);
      const expiry = new Promise<never>((_resolve, reject) => {
        timer = setTimeout(() => {
          controller.abort();
          reject(timeoutError(timeoutMs));
        }, timeoutMs);
      });
      return await Promise.race([this.transport<T>(path, attemptInit), expiry]);
    } catch (error) {
      throw toApiRequestError(error);
    } finally {
      if (timer !== undefined) clearTimeout(timer);
      external?.removeEventListener('abort', forwardAbort);
    }
  }

  /** Read a single resource. */
  get<T>(path: string, options: RequestOptions = {}): Promise<ApiResponse<T>> {
    return this.request<T>(path, { method: 'GET' }, options);
  }

  /**
   * Read one page. Returns the decoded `Page<T>`; use `CursorStore` or
   * `collectPages` to walk further.
   */
  async list<T>(path: string, pagination: Pagination = {}, options: RequestOptions = {}): Promise<Page<T>> {
    const merged: Pagination = {};
    if (pagination.cursor !== undefined) merged.cursor = pagination.cursor;
    if (pagination.limit !== undefined) merged.limit = clampLimit(pagination.limit);
    const response = await this.request<unknown>(path, { method: 'GET' }, { ...options, pagination: merged });
    return decodePage<T>(response.data);
  }

  /**
   * Create or invoke. `POST` is not idempotent, so a key is minted when the
   * caller does not supply one -- otherwise a network blip could not be retried
   * at all, and a blind replay could double-charge.
   */
  post<T>(path: string, body?: unknown, options: RequestOptions = {}): Promise<ApiResponse<T>> {
    return this.mutate<T>('POST', path, body, options);
  }

  /** Partial update. Treated as non-idempotent, exactly like `POST`. */
  patch<T>(path: string, body?: unknown, options: RequestOptions = {}): Promise<ApiResponse<T>> {
    return this.mutate<T>('PATCH', path, body, options);
  }

  /** Full replacement. Idempotent by definition, so no key is minted. */
  put<T>(path: string, body?: unknown, options: RequestOptions = {}): Promise<ApiResponse<T>> {
    return this.mutate<T>('PUT', path, body, options);
  }

  /** Removal. Idempotent by definition, so no key is minted. */
  delete<T>(path: string, options: RequestOptions = {}): Promise<ApiResponse<T>> {
    return this.mutate<T>('DELETE', path, undefined, options);
  }

  /**
   * A mutation that must be exactly-once: always carries an idempotency key so
   * a retry or an offline replay can never post a second sale.
   */
  transaction<T>(path: string, body: unknown, options: RequestOptions = {}): Promise<ApiResponse<T>> {
    const idempotencyKey = options.idempotencyKey ?? createIdempotencyKey();
    return this.mutate<T>('POST', path, body, { ...options, idempotencyKey });
  }

  private mutate<T>(method: string, path: string, body: unknown, options: RequestOptions): Promise<ApiResponse<T>> {
    const resolved: RequestOptions = { ...options };
    if (body !== undefined && resolved.json === undefined) resolved.json = body;
    if (resolved.idempotencyKey === undefined && !isIdempotentMethod(method)) {
      resolved.idempotencyKey = createIdempotencyKey();
    }
    return this.request<T>(path, { method }, resolved);
  }
}

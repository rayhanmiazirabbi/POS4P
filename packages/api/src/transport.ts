import type { ApiResponse } from '@pharmacy/types';

import { ApiRequestError, decodeApiError } from './errors';
import type { ApiTransport } from './client';

/** Minimal `fetch` shape, injected so each platform supplies its own. */
export type FetchLike = (input: string, init?: RequestInit) => Promise<Response>;

export type FetchTransportConfig = {
  fetch?: FetchLike;
  /** Prefixed to the path; use either this or `ApiClientConfig.baseUrl`, not both. */
  baseUrl?: string;
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null;
}

/**
 * Read the success envelope. A response that omits `data`/`requestId` is
 * tolerated rather than fatal, so a proxy that rewrites bodies degrades
 * gracefully instead of breaking the till.
 */
function decodeEnvelope<T>(body: unknown, fallbackRequestId: string): ApiResponse<T> {
  if (isRecord(body) && 'data' in body) {
    const requestId = typeof body['requestId'] === 'string' ? body['requestId'] : fallbackRequestId;
    return { data: body['data'] as T, requestId };
  }
  return { data: body as T, requestId: fallbackRequestId };
}

/**
 * HTTP transport that turns any non-2xx response into an `ApiRequestError`
 * carrying the server's own code verbatim, and a malformed error body into a
 * status-derived fallback rather than a parse crash.
 */
export function createFetchTransport(config: FetchTransportConfig = {}): ApiTransport {
  const fetchImpl = config.fetch ?? (globalThis.fetch as FetchLike | undefined);
  if (fetchImpl === undefined) throw new Error('No fetch implementation available; pass one explicitly');
  const baseUrl = (config.baseUrl ?? '').replace(/\/+$/, '');

  return async <T>(path: string, init: RequestInit): Promise<ApiResponse<T>> => {
    let response: Response;
    try {
      response = await fetchImpl(`${baseUrl}${path}`, init);
    } catch (error) {
      const decoded = decodeApiError(error);
      // A thrown fetch is a transport failure, not a server verdict; only an
      // abort keeps its own code so the retry policy can tell them apart.
      const code = decoded.code === 'ABORTED' || decoded.code === 'TIMEOUT' ? decoded.code : 'NETWORK_ERROR';
      throw new ApiRequestError({ code, message: decoded.message, requestId: decoded.requestId }, null);
    }

    const requestId = response.headers.get('X-Request-ID') ?? 'unknown';
    const raw = await response.text();
    let body: unknown;
    try {
      body = raw === '' ? undefined : (JSON.parse(raw) as unknown);
    } catch {
      body = undefined;
    }

    if (response.ok) return decodeEnvelope<T>(body, requestId);

    const decoded = decodeApiError(body);
    const hasServerCode = isRecord(body) && typeof body['code'] === 'string' && body['code'].trim() !== '';
    const error = {
      code: hasServerCode ? decoded.code : statusFallbackCode(response.status),
      message: decoded.message === 'Request failed' && raw !== '' && !isRecord(body) ? raw : decoded.message,
      requestId: decoded.requestId === 'unknown' ? requestId : decoded.requestId,
      ...(decoded.fieldErrors ? { fieldErrors: decoded.fieldErrors } : {}),
    };
    throw new ApiRequestError(error, response.status);
  };
}

/** Best-effort code when the body carries none, matching the backend's mapping. */
function statusFallbackCode(status: number): string {
  if (status === 400) return 'STORE_CONTEXT_REQUIRED';
  if (status === 401) return 'UNAUTHORIZED';
  if (status === 403) return 'FORBIDDEN';
  if (status === 404) return 'NOT_FOUND';
  if (status === 409) return 'CONFLICT';
  if (status === 422) return 'VALIDATION_ERROR';
  if (status === 429) return 'RATE_LIMITED';
  return 'INTERNAL_ERROR';
}

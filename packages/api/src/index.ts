import type { ApiError, ApiResponse, Pagination } from '@pharmacy/types';

export type StorageAdapter = { get(key: string): Promise<string | null>; set(key: string, value: string): Promise<void>; remove(key: string): Promise<void> };
export type RequestOptions = { signal?: AbortSignal; idempotencyKey?: string; pagination?: Pagination };
export type ApiTransport = <T>(path: string, init: RequestInit) => Promise<ApiResponse<T>>;

export class ApiClient {
  constructor(private readonly transport: ApiTransport, private readonly storage?: StorageAdapter) {}

  async request<T>(path: string, init: RequestInit = {}, options: RequestOptions = {}): Promise<ApiResponse<T>> {
    const headers = new Headers(init.headers);
    const accessToken = await this.storage?.get('access_token');
    if (accessToken) headers.set('Authorization', `Bearer ${accessToken}`);
    if (options.idempotencyKey) headers.set('Idempotency-Key', options.idempotencyKey);
    const requestInit: RequestInit = { ...init, headers };
    if (options.signal) requestInit.signal = options.signal;
    const response = await this.transport<T>(path, requestInit);
    return response;
  }
}

export function decodeApiError(error: unknown): ApiError {
  if (typeof error === 'object' && error !== null && 'code' in error && 'message' in error) {
    const value = error as Partial<ApiError>;
    const decoded: ApiError = { code: value.code ?? 'INTERNAL_ERROR', message: value.message ?? 'Request failed', requestId: value.requestId ?? 'unknown' };
    if (value.fieldErrors) decoded.fieldErrors = value.fieldErrors;
    return decoded;
  }
  return { code: 'INTERNAL_ERROR', message: 'Request failed', requestId: 'unknown' };
}

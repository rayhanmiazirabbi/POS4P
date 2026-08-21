import { expect, it, vi } from 'vitest';
import { ApiClient, decodeApiError, type ApiTransport, type StorageAdapter } from '../src/index';

it('attaches auth and idempotency headers to mutations', async () => {
  const storage: StorageAdapter = {
    get: vi.fn(async () => 'access-token'), set: vi.fn(async () => undefined), remove: vi.fn(async () => undefined),
  };
  let captured: RequestInit | undefined;
  const transport: ApiTransport = async <T>(_path: string, init: RequestInit) => {
    captured = init;
    return { data: undefined as T, requestId: 'request-1' };
  };
  const client = new ApiClient(transport, storage);
  await client.request('/sales', { method: 'POST' }, { idempotencyKey: 'event-1234567890' });
  const headers = new Headers(captured?.headers);
  expect(headers.get('Authorization')).toBe('Bearer access-token');
  expect(headers.get('Idempotency-Key')).toBe('event-1234567890');
});

it('preserves structured server error codes', () => {
  expect(decodeApiError({ code: 'INSUFFICIENT_STOCK', message: 'No stock', requestId: 'r1' }).code).toBe('INSUFFICIENT_STOCK');
  expect(decodeApiError(new Error('network')).code).toBe('INTERNAL_ERROR');
});

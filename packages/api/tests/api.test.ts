import { describe, expect, it, vi } from 'vitest';
import {
  ApiClient, CursorStore, createAuthClient, createMemoryStorage, createPharmacyApi,
  isApiRequestError, storageKeys, type ApiTransport, type StorageAdapter,
} from '../src/index';

function capturingTransport<T = unknown>(
  handler: (path: string, init: RequestInit, call: number) => ApiResponseShape<T> | Promise<ApiResponseShape<T>>,
): { transport: ApiTransport; calls: Array<{ path: string; init: RequestInit }> } {
  const calls: Array<{ path: string; init: RequestInit }> = [];
  let call = 0;
  const transport: ApiTransport = async <TActual>(path: string, init: RequestInit) => {
    calls.push({ path, init });
    call += 1;
    return (await handler(path, init, call)) as unknown as ApiResponseShape<TActual>;
  };
  return { transport, calls };
}

type ApiResponseShape<T> = { data: T; requestId: string };

describe('ApiClient', () => {
  it('attaches auth and idempotency headers to mutations', async () => {
    const storage: StorageAdapter = {
      get: vi.fn(async () => 'access-token'), set: vi.fn(async () => undefined), remove: vi.fn(async () => undefined),
    };
    const { transport, calls } = capturingTransport(() => ({ data: undefined, requestId: 'request-1' }));
    const client = new ApiClient(transport, storage);
    await client.request('/sales', { method: 'POST' }, { idempotencyKey: 'event-1234567890' });
    const headers = new Headers(calls[0]?.init.headers);
    expect(headers.get('Authorization')).toBe('Bearer access-token');
    expect(headers.get('Idempotency-Key')).toBe('event-1234567890');
  });

  it('surfaces TIMEOUT when the wall-clock budget expires', async () => {
    const hanging: ApiTransport = () => new Promise(() => undefined); // never settles
    const client = new ApiClient(hanging, undefined, { timeoutMs: 10, sleep: async () => undefined });
    const error = await client.get('/stores').catch((caught: unknown) => caught);
    expect(isApiRequestError(error)).toBe(true);
    expect((error as { code: string }).code).toBe('TIMEOUT');
  });

  it('retries with exponential backoff through the injected sleep', async () => {
    const sleeps: number[] = [];
    const { transport } = capturingTransport((_path, _init, call) => {
      if (call < 3) throw Object.assign(new Error('socket hang up'), { name: 'TypeError' });
      return { data: { ok: true }, requestId: 'r2' };
    });
    const client = new ApiClient(transport, undefined, {
      retry: { attempts: 3, baseDelayMs: 100, maxDelayMs: 1_000, jitterRatio: 0 },
      sleep: async (ms) => { sleeps.push(ms); },
    });
    const response = await client.get('/stores');
    expect(response.data).toEqual({ ok: true });
    expect(sleeps).toEqual([100, 200]); // base * 2 ** (n - 1), deterministic without jitter
  });

  it('never replays a non-idempotent POST without an idempotency key', async () => {
    // The rule that stops a double-charged sale: a timed-out POST may have
    // committed server-side, so it must surface, not retry.
    const { transport, calls } = capturingTransport(() => {
      throw Object.assign(new Error('aborted'), { name: 'AbortError' });
    });
    const client = new ApiClient(transport, undefined, { sleep: async () => undefined });
    const error = await client.request('/sales', { method: 'POST' }).catch((caught: unknown) => caught);
    expect(isApiRequestError(error)).toBe(true);
    expect(calls).toHaveLength(1);
  });

  it('reuses one idempotency key across retries of a mutation', async () => {
    const keys: (string | null)[] = [];
    const { transport } = capturingTransport((_path, init, call) => {
      keys.push(new Headers(init.headers).get('Idempotency-Key'));
      if (call < 2) throw Object.assign(new Error('socket hang up'), { name: 'TypeError' });
      return { data: { id: 'sale-1' }, requestId: 'r3' };
    });
    const client = new ApiClient(transport, undefined, { sleep: async () => undefined });
    await client.post('/sales', { total: '10.00' });
    expect(keys).toHaveLength(2);
    expect(keys[1]).toBe(keys[0]);
    expect(keys[0]).not.toBeNull();
  });

  it('decodes malformed and garbled error bodies without throwing', async () => {
    const garbled: ApiTransport = async () => {
      throw '<<<html><body>502 Bad Gateway';
    };
    const client = new ApiClient(garbled, undefined, { sleep: async () => undefined });
    const error = await client.get('/stores').catch((caught: unknown) => caught);
    expect(isApiRequestError(error)).toBe(true);
    expect((error as { code: string }).code).toBe('INTERNAL_ERROR');
  });
});

describe('CursorStore', () => {
  it('persists and resumes a cursor across restarts', async () => {
    const storage = createMemoryStorage();
    const first = new CursorStore(storage, 'sync');
    expect(await first.resume('sales')).toEqual({});
    await first.advance('sales', { items: [], nextCursor: 'cursor-9' });

    // A fresh instance over the same storage models an app restart.
    const second = new CursorStore(storage, 'sync');
    expect(await second.resume('sales', 50)).toEqual({ cursor: 'cursor-9', limit: 50 });
    await second.advance('sales', { items: [], nextCursor: null });
    expect(await second.read('sales')).toBeNull();
  });
});

describe('auth client', () => {
  const storage = createMemoryStorage({ [storageKeys.accessToken]: 'token-1' });

  function authFixture() {
    const { transport, calls } = capturingTransport(() => ({ data: undefined, requestId: 'r4' }));
    return { calls, api: createPharmacyApi(new ApiClient(transport, storage)) };
  }

  it('maps every /auth endpoint with the exact wire contract', async () => {
    const { calls, api } = authFixture();
    await api.auth.requestOtp({ phone: '01700000000' });
    await api.auth.verifyOtp({ challengeId: '00000000-0000-7000-8000-000000000001', code: '123456' });
    await api.auth.loginWithPin({ phone: '01700000000', pin: '1234', organizationId: '00000000-0000-7000-8000-000000000002' });
    await api.auth.refresh({ refreshToken: 'refresh-token-value-16' });
    await api.auth.selectContext({ organizationId: '00000000-0000-7000-8000-000000000003' });
    await api.auth.me();
    await api.auth.listSessions({ userId: '00000000-0000-7000-8000-000000000004' });
    await api.auth.logout();
    await api.auth.revokeSession('00000000-0000-7000-8000-000000000005');
    await api.auth.registerDevice({ deviceKey: 'device-key-1234', name: 'Counter tablet' });
    await api.auth.listDevices();
    await api.auth.revokeDevice('00000000-0000-7000-8000-000000000006');

    expect(calls.map(({ path }) => path)).toEqual([
      '/auth/otp/request', '/auth/otp/verify', '/auth/pin/login', '/auth/refresh', '/auth/context',
      '/auth/me', '/auth/sessions?user_id=00000000-0000-7000-8000-000000000004', '/auth/logout',
      '/auth/sessions/00000000-0000-7000-8000-000000000005/revoke', '/auth/devices',
      '/auth/devices', '/auth/devices/00000000-0000-7000-8000-000000000006/revoke',
    ]);
  });

  it('keeps pre-session calls anonymous and the rest bearer-authenticated', async () => {
    const { calls, api } = authFixture();
    await api.auth.requestOtp({ phone: '01700000000' });
    await api.auth.me();
    const [otp, me] = calls.map(({ init }) => new Headers(init.headers).get('Authorization'));
    expect(otp).toBeNull();
    expect(me).toBe('Bearer token-1');
  });

  it('sends the login device claim and the register body under their distinct keys', async () => {
    const { calls, api } = authFixture();
    await api.auth.loginWithPin({
      phone: '01700000000', pin: '1234', organizationId: '00000000-0000-7000-8000-000000000002',
      device: { deviceKey: 'device-key-1234', deviceName: 'Counter tablet' },
    });
    await api.auth.registerDevice({ deviceKey: 'device-key-1234', name: 'Counter tablet' });
    const [login, register] = calls.map(({ init }) => JSON.parse(String(init.body)));
    expect(login).toMatchObject({ device: { deviceKey: 'device-key-1234', deviceName: 'Counter tablet' } });
    expect(register).toEqual({ deviceKey: 'device-key-1234', name: 'Counter tablet' });
  });

  it('is reachable through the standalone factory too', async () => {
    const { transport, calls } = capturingTransport(() => ({ data: undefined, requestId: 'r5' }));
    const auth = createAuthClient(new ApiClient(transport, storage));
    await auth.logout();
    expect(calls[0]?.path).toBe('/auth/logout');
  });
});

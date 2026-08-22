import { storageKeys, type StorageAdapter } from '@pharmacy/api';
import type { Session } from '@pharmacy/types';

export type AuthState = { session: Session | null; deviceId: string | null };
export type AuthAdapter = { storage: StorageAdapter; refresh(): Promise<Session>; logout(): Promise<void> };

export class SessionManager {
  constructor(private readonly adapter: AuthAdapter) {}

  async restore(): Promise<Session | null> {
    const serialized = await this.adapter.storage.get(storageKeys.session);
    if (!serialized) return null;
    try { return JSON.parse(serialized) as Session; } catch { await this.clear(); return null; }
  }

  async persist(session: Session): Promise<void> {
    await this.adapter.storage.set(storageKeys.accessToken, session.accessToken);
    // The refresh token must survive the process: without it an app restart
    // cannot rotate credentials and the user is dumped back at the OTP screen.
    await this.adapter.storage.set(storageKeys.refreshToken, session.refreshToken);
    await this.adapter.storage.set(storageKeys.session, JSON.stringify(session));
  }

  async refresh(): Promise<Session> { const session = await this.adapter.refresh(); await this.persist(session); return session; }
  async logout(): Promise<void> { await this.adapter.logout(); await this.clear(); }

  private async clear(): Promise<void> {
    await this.adapter.storage.remove(storageKeys.accessToken);
    await this.adapter.storage.remove(storageKeys.refreshToken);
    await this.adapter.storage.remove(storageKeys.session);
  }
}

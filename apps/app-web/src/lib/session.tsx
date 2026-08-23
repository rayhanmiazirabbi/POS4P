'use client';

import { storageKeys, type CurrentUser, type MembershipOption, type TokenBundle } from '@pharmacy/api';
import { useEffect, type ReactNode } from 'react';
import { create } from 'zustand';

import { dexieStorage } from '../platform/dexie';
import { pharmacyApi } from './api';

export type SessionState = {
  user: CurrentUser | null;
  status: 'loading' | 'signed-out' | 'signed-in';
};

export type SessionStore = SessionState & {
  signIn: (bundle: TokenBundle) => Promise<void>;
  signOut: () => Promise<void>;
};

async function persistBundle(bundle: TokenBundle): Promise<void> {
  await dexieStorage.set(storageKeys.accessToken, bundle.accessToken);
  await dexieStorage.set(storageKeys.refreshToken, bundle.refreshToken);
  await dexieStorage.set(storageKeys.session, JSON.stringify(bundle));
}

/**
 * The session as a store, not context. Same shape `useSession` always answered
 * with, so consumers did not move; the difference is that state lives outside
 * the React tree, which the login flow -- spread across route groups and
 * redirects -- used to keep alive only by mounting one provider above
 * everything.
 */
const useSessionStore = create<SessionStore>()(() => ({
  user: null,
  status: 'loading',
  signIn: async (bundle) => {
    await persistBundle(bundle);
    const response = await pharmacyApi.auth.me();
    useSessionStore.setState({ user: response.data, status: 'signed-in' });
  },
  signOut: async () => {
    try {
      await pharmacyApi.auth.logout();
    } finally {
      await dexieStorage.remove(storageKeys.accessToken);
      await dexieStorage.remove(storageKeys.refreshToken);
      await dexieStorage.remove(storageKeys.session);
      useSessionStore.setState({ user: null, status: 'signed-out' });
    }
  },
}));

let bootstrap: Promise<void> | null = null;

/** Resolve the persisted token into a session once per page load, whoever mounts first. */
function bootstrapSession(): Promise<void> {
  bootstrap ??= (async () => {
    try {
      const response = await pharmacyApi.auth.me();
      useSessionStore.setState({ user: response.data, status: 'signed-in' });
    } catch {
      await dexieStorage.remove(storageKeys.accessToken);
      await dexieStorage.remove(storageKeys.refreshToken);
      useSessionStore.setState({ user: null, status: 'signed-out' });
    }
  })();
  return bootstrap;
}

/** Kicks off the one-time token check; children render either way. */
export function SessionProvider({ children }: { children: ReactNode }): ReactNode {
  useEffect(() => {
    void bootstrapSession();
  }, []);
  return <>{children}</>;
}

export function useSession(): SessionStore {
  return useSessionStore();
}

/** Organizations the signed-in user may pin the token to; empty once a context is chosen. */
export function membershipOptions(bundle: TokenBundle): readonly MembershipOption[] {
  return bundle.organizations;
}

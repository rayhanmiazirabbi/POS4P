import type { CurrentUser, TokenBundle } from '@pharmacy/api';
import { storageKeys } from '@pharmacy/api';
import { useEffect, type ReactNode } from 'react';
import { create } from 'zustand';

import { pharmacyApi } from './api';

export type SessionStatus = 'loading' | 'signed-out' | 'signed-in';

export type SessionStore = {
  user: CurrentUser | null;
  status: SessionStatus;
  signIn: (bundle: TokenBundle) => Promise<void>;
  signOut: () => Promise<void>;
  restore: () => Promise<void>;
};

/**
 * Session state lives in a Zustand store: one source outside the component
 * tree, so any screen can read who is signed in without a provider hop, while
 * persistence is unchanged -- tokens still route through the SecureStore
 * adapter and nothing about them ever lands in plain SQLite.
 */
export const useSessionStore = create<SessionStore>((set) => ({
  user: null,
  status: 'loading',

  signIn: async (bundle) => {
    const { secureStoreAdapter } = await import('../platform/nativeAdapters');
    await secureStoreAdapter.setItem(storageKeys.accessToken, bundle.accessToken);
    await secureStoreAdapter.setItem(storageKeys.refreshToken, bundle.refreshToken);
    const response = await pharmacyApi.auth.me();
    set({ user: response.data, status: 'signed-in' });
  },

  signOut: async () => {
    try {
      await pharmacyApi.auth.logout();
    } finally {
      const { secureStoreAdapter } = await import('../platform/nativeAdapters');
      await secureStoreAdapter.deleteItem(storageKeys.accessToken);
      await secureStoreAdapter.deleteItem(storageKeys.refreshToken);
      set({ user: null, status: 'signed-out' });
    }
  },

  restore: async () => {
    // The tokens themselves are read by the API client from SecureStore; this
    // only asks the server who they belong to now.
    try {
      const response = await pharmacyApi.auth.me();
      set({ user: response.data, status: 'signed-in' });
    } catch {
      set({ user: null, status: 'signed-out' });
    }
  },
}));

export function useSession(): SessionStore {
  return useSessionStore();
}

/** Mount point kept for the root layout; fires the one startup restore. */
export function SessionProvider({ children }: { children: ReactNode }): ReactNode {
  useEffect(() => {
    void useSessionStore.getState().restore();
  }, []);
  return children;
}

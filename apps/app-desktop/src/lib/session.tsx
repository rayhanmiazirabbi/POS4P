import type { CurrentUser, TokenBundle } from '@pharmacy/api';
import { storageKeys } from '@pharmacy/api';
import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react';

import { desktopPlatform } from '../platform/runtime';
import { pharmacyApi } from './api';
import { rememberTerminal } from './terminal';

type SessionContextValue = {
  user: CurrentUser | null;
  status: 'loading' | 'signed-out' | 'signed-in';
  signIn: (bundle: TokenBundle) => Promise<void>;
  signOut: () => Promise<void>;
};

const SessionContext = createContext<SessionContextValue | null>(null);

export function SessionProvider({ children }: { children: ReactNode }): ReactNode {
  const [user, setUser] = useState<CurrentUser | null>(null);
  const [status, setStatus] = useState<'loading' | 'signed-out' | 'signed-in'>('loading');

  useEffect(() => {
    let cancelled = false;
    void pharmacyApi.auth
      .me()
      .then((response) => {
        if (!cancelled) {
          setUser(response.data);
          setStatus('signed-in');
        }
        // A restored session refreshes the binding too, so a till moved between
        // branches by an admin stops offering PIN unlock into the old one.
        return rememberTerminal(response.data);
      })
      .catch(() => {
        if (!cancelled) setStatus('signed-out');
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const signIn = useCallback(async (bundle: TokenBundle) => {
    const store = (await desktopPlatform()).database;
    await store.set(storageKeys.accessToken, bundle.accessToken);
    await store.set(storageKeys.refreshToken, bundle.refreshToken);
    const response = await pharmacyApi.auth.me();
    // Recorded from `me()`, never from the bundle: the bundle's store may be null
    // on a multi-branch account, and `me()` reports the row the token resolved to.
    await rememberTerminal(response.data);
    setUser(response.data);
    setStatus('signed-in');
  }, []);

  const signOut = useCallback(async () => {
    try {
      await pharmacyApi.auth.logout();
    } finally {
      const store = (await desktopPlatform()).database;
      await store.remove(storageKeys.accessToken);
      await store.remove(storageKeys.refreshToken);
      // The terminal binding stays. A shift change is not a change of shop, and
      // dropping it here would put the next cashier back on an SMS code -- which
      // is the whole thing PIN unlock exists to avoid.
      setUser(null);
      setStatus('signed-out');
    }
  }, []);

  const value = useMemo<SessionContextValue>(() => ({ user, status, signIn, signOut }), [user, status, signIn, signOut]);
  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>;
}

export function useSession(): SessionContextValue {
  const value = useContext(SessionContext);
  if (value === null) throw new Error('useSession must be used inside <SessionProvider>');
  return value;
}

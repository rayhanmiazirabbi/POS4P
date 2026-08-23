'use client';

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { useState, type ReactNode } from 'react';

/**
 * Owns every server read's cache (`app-web.md`: "TanStack Query owns API cache").
 *
 * The client is created in state, not at module scope, so a render pass on the
 * server never shares a cache with a browser -- and two browser tabs never share
 * one either.
 */
export function QueryProvider({ children }: { children: ReactNode }): ReactNode {
  const [client] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            staleTime: 30_000,
            refetchOnWindowFocus: true,
          },
        },
      }),
  );
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

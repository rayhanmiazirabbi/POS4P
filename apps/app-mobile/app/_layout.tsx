import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { Stack } from 'expo-router';
import { useState } from 'react';

import { SessionProvider } from '../src/lib/session';

export default function Layout() {
  // One client for the app's lifetime; it owns server state (shelf, queue
  // status, lookups) while session state stays in its Zustand store.
  const [queryClient] = useState(() => new QueryClient());
  return (
    <QueryClientProvider client={queryClient}>
      <SessionProvider>
        <Stack>
          <Stack.Screen name="index" options={{ title: 'Pharmacy POS' }} />
          <Stack.Screen name="(auth)/login" options={{ title: 'Sign in' }} />
          <Stack.Screen name="(pos)/pos" options={{ title: 'Counter' }} />
          <Stack.Screen name="(pos)/sync" options={{ title: 'Sync status' }} />
        </Stack>
      </SessionProvider>
    </QueryClientProvider>
  );
}

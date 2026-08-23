import { tokens } from '@pharmacy/design-tokens';
import type { Viewport } from 'next';
import type { ReactNode } from 'react';

import { QueryProvider } from '@/lib/query-provider';
import { SessionProvider } from '@/lib/session';

import './globals.css';

export const viewport: Viewport = {
  width: 'device-width',
  initialScale: 1,
};

export default function RootLayout({ children }: Readonly<{ children: ReactNode }>) {
  return (
    <html lang="en">
      <body style={{ margin: 0, fontFamily: tokens.typography.family }}>
        <QueryProvider>
          <SessionProvider>{children}</SessionProvider>
        </QueryProvider>
      </body>
    </html>
  );
}

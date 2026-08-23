import { colors, spacing, tokens } from '@pharmacy/design-tokens';
import { can } from '@pharmacy/permissions';
import type { ReactNode } from 'react';

import { SessionProvider, useSession } from './lib/session';
import { LoginScreen } from './screens/LoginScreen';
import { PosScreen } from './screens/PosScreen';

function Gate(): ReactNode {
  const { status, user, signOut } = useSession();
  if (status === 'signed-out') return <LoginScreen />;
  if (status !== 'signed-in' || user === null) return <main style={{ padding: 32 }}>Loading…</main>;
  // This shell is one screen and that screen is a till. A role without
  // `sales.create` -- inventory staff -- would get a counter whose only action the
  // server refuses, and would find that out one cart at a time. Said up front
  // instead, with the way back out, because on a shared till the likely cause is
  // the wrong person's PIN.
  if (!can(user.role, 'sales.create')) return <NotACashier role={user.role} onSignOut={() => void signOut()} />;
  return <PosScreen />;
}

function NotACashier({ role, onSignOut }: { role: string; onSignOut: () => void }): ReactNode {
  return (
    <main style={{ minHeight: '100vh', display: 'grid', placeItems: 'center', background: colors.background, color: colors.foreground, fontFamily: tokens.typography.family }}>
      <div style={{ background: colors.surface, border: `1px solid ${colors.border}`, borderRadius: 12, padding: spacing['2xl'], width: 380, textAlign: 'center' }}>
        <h1 style={{ marginTop: 0, fontSize: tokens.typography.sizes.lg }}>This till is for cashiers</h1>
        <p role="alert" style={{ color: colors.muted }}>
          Your role ({role}) cannot record sales. Sign in as a cashier, or use the web app for stock and reporting work.
        </p>
        <button type="button" onClick={onSignOut} style={{ width: '100%', padding: spacing.md, borderRadius: 8, border: 'none', background: colors.primary, color: colors.primaryForeground, fontWeight: tokens.typography.weights.semibold, cursor: 'pointer' }}>
          Sign out
        </button>
      </div>
    </main>
  );
}

export function App(): ReactNode {
  return (
    <SessionProvider>
      <Gate />
    </SessionProvider>
  );
}

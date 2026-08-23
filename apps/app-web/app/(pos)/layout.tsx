'use client';

import { colors, spacing, tokens } from '@pharmacy/design-tokens';
import Link from 'next/link';
import { usePathname, useRouter } from 'next/navigation';
import { useEffect, type ReactNode } from 'react';

import { allowedRoutes, isUnder, landingRoute, mayVisit, routeFor } from '@/lib/navigation';
import { useSession } from '@/lib/session';

export default function PosLayout({ children }: { children: ReactNode }): ReactNode {
  const { user, status, signOut } = useSession();
  const router = useRouter();
  const pathname = usePathname();

  const role = user?.role ?? null;
  const known = routeFor(pathname) !== undefined;
  const permitted = mayVisit(role, pathname);
  const allowed = allowedRoutes(role);
  const landing = landingRoute(role);

  useEffect(() => {
    if (status === 'signed-out') router.replace('/login');
  }, [status, router]);

  useEffect(() => {
    // Sign-in always lands on `/pos`, because the role is not known at that point
    // -- it arrives with `me()`. Inventory staff hold no `sales.create`, so without
    // this they opened a counter the server refuses every sale from.
    if (status !== 'signed-in' || !known || permitted || landing === null) return;
    router.replace(landing);
  }, [status, known, permitted, landing, router]);

  if (status !== 'signed-in' || user === null) {
    return <main style={{ padding: spacing['2xl'], fontFamily: tokens.typography.family }}>Loading…</main>;
  }

  return (
    <div style={{ minHeight: '100vh', background: colors.background, color: colors.foreground, fontFamily: tokens.typography.family }}>
      {/* Wrapping, not shrinking: on a counter's narrow window the links and the
          signed-in label must flow onto another line, not overflow off-screen. */}
      <header style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: spacing.xl, rowGap: spacing.sm, padding: `${spacing.md} ${spacing.xl}`, background: colors.surface, borderBottom: `1px solid ${colors.border}` }}>
        <strong>{user.organizationName}</strong>
        <nav style={{ display: 'flex', flexWrap: 'wrap', gap: spacing.lg, rowGap: spacing.sm, flex: 1 }}>
          {allowed.map((route) => <NavLink key={route.href} href={route.href} pathname={pathname}>{route.label}</NavLink>)}
        </nav>
        <span style={{ color: colors.muted }}>
          {user.user.displayName} · {user.role}
          {user.storeName ? ` · ${user.storeName}` : ''}
        </span>
        <button type="button" onClick={() => void signOut()} style={{ padding: `${spacing.xs} ${spacing.md}`, borderRadius: 8, border: `1px solid ${colors.border}`, background: 'transparent', cursor: 'pointer' }}>
          Sign out
        </button>
      </header>
      {/* Children are withheld, not merely unlinked. A denied page that mounts runs
          its own loader first, so typing the URL earned a screenful of 403s on a
          screen the role should never have reached -- hiding the link was never the
          guard. Backend enforcement stays mandatory either way; this only stops the
          client asking questions it has no right to ask. */}
      {permitted ? children : <Denied role={user.role} landing={landing} />}
    </div>
  );
}

/** Shown while the redirect is in flight, and permanently for a role that holds
 *  nothing: there is nowhere to send them, and bouncing to `/login` would only
 *  sign them straight back in and land here again. */
function Denied({ role, landing }: { role: string; landing: string | null }): ReactNode {
  return (
    <main style={{ padding: spacing['2xl'] }}>
      <p role="alert" style={{ color: colors.muted, margin: 0 }}>
        {landing === null
          ? `Your role (${role}) has no screens assigned. Ask an owner or manager to review your access.`
          : 'Not available for your role — taking you back…'}
      </p>
    </main>
  );
}

function NavLink({ href, pathname, children }: { href: string; pathname: string; children: ReactNode }): ReactNode {
  const active = isUnder(pathname, href);
  return (
    <Link href={href} style={{ color: active ? colors.primary : colors.muted, textDecoration: 'none', fontWeight: active ? tokens.typography.weights.semibold : tokens.typography.weights.regular }}>
      {children}
    </Link>
  );
}

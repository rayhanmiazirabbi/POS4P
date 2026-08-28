'use client';

import Link from 'next/link';
import { usePathname, useRouter } from 'next/navigation';
import { useEffect, useState, type ReactNode } from 'react';

import { allowedRoutes, isUnder, landingRoute, mayVisit, routeFor } from '@/lib/navigation';
import { routeForShortcut } from '@/lib/shortcuts';
import { useSession } from '@/lib/session';

export default function PosLayout({ children }: { children: ReactNode }): ReactNode {
  const { user, status, signOut } = useSession();
  const router = useRouter();
  const pathname = usePathname();
  const [shortcutsOpen, setShortcutsOpen] = useState(false);

  const role = user?.role ?? null;
  const known = routeFor(pathname) !== undefined;
  const permitted = mayVisit(role, pathname);
  const allowed = allowedRoutes(role);
  const landing = landingRoute(role);

  useEffect(() => {
    if (status === 'signed-out') router.replace('/login');
  }, [status, router]);

  useEffect(() => {
    if (status !== 'signed-in' || !known || permitted || landing === null) return;
    router.replace(landing);
  }, [status, known, permitted, landing, router]);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent): void => {
      if (document.querySelector('[aria-modal="true"]')) return;
      const route = routeForShortcut(event.key, event.altKey);
      if (route !== null && mayVisit(role, route)) {
        event.preventDefault();
        router.push(route);
        return;
      }
      const editable = event.target instanceof HTMLElement && event.target.matches('input, textarea, select, [contenteditable="true"]');
      if (event.key === '?' && !event.altKey && !event.ctrlKey && !event.metaKey && !editable) {
        event.preventDefault();
        setShortcutsOpen(true);
      }
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [role, router]);

  if (status !== 'signed-in' || user === null) return <main className="route-loading">Loading workspace…</main>;

  return (
    <div className="app-shell">
      <header className="app-header">
        <div className="brand-block"><span className="brand-mark" aria-hidden="true">Rx</span><span className="brand-name">{user.organizationName}</span></div>
        <nav className="app-nav" aria-label="Primary navigation">
          {allowed.map((route) => <NavLink key={route.href} href={route.href} pathname={pathname}>{route.label}</NavLink>)}
        </nav>
        <div className="account-context">
          <span className="account-copy"><strong>{user.user.displayName}</strong><small>{user.role.replace('_', ' ')}{user.storeName ? ` · ${user.storeName}` : ''}</small></span>
          <button type="button" className="header-action" onClick={() => setShortcutsOpen(true)} aria-label="Show keyboard shortcuts">?</button>
          <button type="button" className="header-action sign-out" onClick={() => void signOut()}>Sign out</button>
        </div>
      </header>
      <div className="app-content">{permitted ? children : <Denied role={user.role} landing={landing} />}</div>
      {shortcutsOpen && <ShortcutDialog onClose={() => setShortcutsOpen(false)} />}
    </div>
  );
}

function Denied({ role, landing }: { role: string; landing: string | null }): ReactNode {
  return <main className="page-shell"><p role="alert" className="status-message status-message--muted">{landing === null ? `Your role (${role}) has no screens assigned. Ask an owner or manager to review your access.` : 'Not available for your role — taking you back…'}</p></main>;
}

function NavLink({ href, pathname, children }: { href: string; pathname: string; children: ReactNode }): ReactNode {
  const active = isUnder(pathname, href);
  return <Link href={href} className={`nav-link${active ? ' nav-link--active' : ''}`} aria-current={active ? 'page' : undefined}>{children}</Link>;
}

function ShortcutDialog({ onClose }: { onClose: () => void }): ReactNode {
  return (
    <div className="dialog-backdrop" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
      <section className="dialog-panel shortcut-dialog" role="dialog" aria-modal="true" aria-labelledby="shortcuts-title" onKeyDown={(event) => { if (event.key === 'Escape') onClose(); }}>
        <header className="dialog-header"><div><span className="eyebrow">Keyboard operation</span><h2 id="shortcuts-title">Shortcuts</h2></div><button autoFocus type="button" className="icon-action" onClick={onClose} aria-label="Close shortcuts">×</button></header>
        <div className="shortcut-grid">
          <kbd>Alt 1–6</kbd><span>Navigate the main workspace</span>
          <kbd>/</kbd><span>Focus medicine search</span>
          <kbd>↑ ↓ Enter</kbd><span>Move through and select results</span>
          <kbd>F4</kbd><span>Hold the current cart</span>
          <kbd>F8</kbd><span>Focus held carts</span>
          <kbd>F9 / F10</kbd><span>Focus cash / digital amount</span>
          <kbd>F12</kbd><span>Complete the sale</span>
          <kbd>Esc</kbd><span>Close the current panel</span>
        </div>
      </section>
    </div>
  );
}

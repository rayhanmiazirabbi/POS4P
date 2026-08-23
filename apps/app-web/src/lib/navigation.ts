import { can, type Capability } from '@pharmacy/permissions';
import type { Role } from '@pharmacy/types';

export type PosRoute = { href: string; label: string; capability: Capability };

/**
 * Every authenticated surface and the capability that opens it.
 *
 * The capability is the point. The layout used to ask `user.role === 'owner' ||
 * user.role === 'manager'`, a second answer to a question `@pharmacy/permissions`
 * already answers -- so a role added to the matrix reached none of these screens,
 * and a grant moved between roles changed nothing here. Each capability below is
 * the one its router guards on the backend, so what the nav offers and what the
 * server will accept are the same set. `navigationMatchesBackend` in the tests
 * pins that correspondence.
 *
 * Order matters: the first entry a role holds is where that role lands.
 */
export const POS_ROUTES: readonly PosRoute[] = [
  { href: '/pos', label: 'POS', capability: 'sales.create' },
  { href: '/dashboard', label: 'Dashboard', capability: 'reports.read' },
  { href: '/catalogue', label: 'Catalogue', capability: 'store.manage' },
  { href: '/inventory', label: 'Inventory', capability: 'inventory.adjust' },
  { href: '/purchasing', label: 'Purchasing', capability: 'purchases.manage' },
];

/** Whether `pathname` is `href` or something nested under it -- segment-aware, so
 *  a future `/posters` is not mistaken for `/pos`. */
export function isUnder(pathname: string, href: string): boolean {
  return pathname === href || pathname.startsWith(`${href}/`);
}

export function routeFor(pathname: string): PosRoute | undefined {
  return POS_ROUTES.find((route) => isUnder(pathname, route.href));
}

export function allowedRoutes(role: Role | null): readonly PosRoute[] {
  return role === null ? [] : POS_ROUTES.filter((route) => can(role, route.capability));
}

/** Where a role goes when it has no business on the route it asked for. `null`
 *  means it holds nothing at all, which is a different situation from a wrong
 *  turn and has to be said rather than redirected. */
export function landingRoute(role: Role | null): string | null {
  return allowedRoutes(role)[0]?.href ?? null;
}

export function mayVisit(role: Role | null, pathname: string): boolean {
  const route = routeFor(pathname);
  return route !== undefined && role !== null && can(role, route.capability);
}

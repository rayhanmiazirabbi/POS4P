import type { Role } from '@pharmacy/types';
import { describe, expect, it } from 'vitest';

import { POS_ROUTES, allowedRoutes, isUnder, landingRoute, mayVisit, routeFor } from './navigation';

const ROLES: readonly Role[] = ['owner', 'manager', 'cashier', 'inventory_staff'];

const hrefs = (role: Role): string[] => allowedRoutes(role).map((route) => route.href);

describe('route resolution', () => {
  it('matches a route by segment, not by prefix', () => {
    expect(routeFor('/pos')?.href).toBe('/pos');
    expect(routeFor('/pos/receipt/abc')?.href).toBe('/pos');
    // A sibling route sharing a prefix must not resolve to `/pos`; the old
    // `startsWith` in the layout would have handed it the counter's capability.
    expect(routeFor('/posters')).toBeUndefined();
    expect(routeFor('/nowhere')).toBeUndefined();
    expect(isUnder('/inventory/batches', '/inventory')).toBe(true);
    expect(isUnder('/inventoryx', '/inventory')).toBe(false);
  });
});

describe('navigation by role', () => {
  it('gives owner and manager every surface', () => {
    for (const role of ['owner', 'manager'] as const) {
      expect(hrefs(role)).toEqual(['/pos', '/dashboard', '/catalogue', '/inventory', '/purchasing', '/settings']);
    }
  });

  it('gives a cashier the counter, dashboard, catalogue search, and purchase orders', () => {
    expect(hrefs('cashier')).toEqual(['/pos', '/dashboard', '/catalogue', '/purchasing']);
    expect(mayVisit('cashier', '/catalogue')).toBe(true);
    expect(mayVisit('cashier', '/purchasing')).toBe(true);
    expect(mayVisit('cashier', '/inventory')).toBe(false);
    expect(mayVisit('cashier', '/settings')).toBe(false);
  });

  it('opens the counter in receive-only mode for inventory staff', () => {
    expect(hrefs('inventory_staff')).toEqual(['/pos', '/dashboard', '/catalogue', '/inventory', '/purchasing']);
    expect(mayVisit('inventory_staff', '/pos')).toBe(true);
    expect(mayVisit('inventory_staff', '/purchasing')).toBe(true);
  });

  it('lands every role on a surface it actually holds', () => {
    // `/login` sends everyone to `/pos` because the role is unknown until `me()`
    // resolves. Whatever the landing is, the guard must not bounce it away again.
    for (const role of ROLES) {
      const landing = landingRoute(role);
      expect(landing, role).not.toBeNull();
      expect(mayVisit(role, landing as string), role).toBe(true);
    }
    expect(landingRoute('cashier')).toBe('/pos');
    expect(landingRoute('inventory_staff')).toBe('/pos');
  });

  it('treats an unknown route and an unknown role as denied', () => {
    // Default deny: `mayVisit` answers on the capability matrix, and an
    // unrecognised path has no capability to check.
    expect(mayVisit('owner', '/not-a-route')).toBe(false);
    expect(mayVisit(null, '/pos')).toBe(false);
    expect(allowedRoutes(null)).toEqual([]);
    expect(landingRoute(null)).toBeNull();
  });
});

describe('the route table itself', () => {
  it('declares a distinct capability-bearing entry per surface', () => {
    expect(new Set(POS_ROUTES.map((route) => route.href)).size).toBe(POS_ROUTES.length);
    for (const route of POS_ROUTES) {
      expect(route.href.startsWith('/'), route.href).toBe(true);
      expect(route.label.length).toBeGreaterThan(0);
      // Every surface is reachable by somebody, or it is dead code behind a
      // capability no role holds.
      expect(ROLES.some((role) => mayVisit(role, route.href)), route.href).toBe(true);
    }
  });
});

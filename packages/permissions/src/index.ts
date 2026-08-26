import { domainError, type DomainError } from '@pharmacy/core';
import type { Role, StoreMembership } from '@pharmacy/types';

export type Capability =
  | 'organization.manage' | 'store.manage' | 'users.manage' | 'sales.create'
  | 'sales.refund' | 'inventory.read' | 'inventory.adjust' | 'purchases.manage'
  | 'purchasing.orders.manage' | 'catalogue.search' | 'products.adopt'
  | 'reports.read' | 'reports.read_costs';

/**
 * The single answer to "may this role do that".
 *
 * `purchases.manage` deliberately stops at manager. Entering a purchase *is*
 * entering supplier unit costs, so granting it to a role that is denied
 * `reports.read_costs` would contradict this same table: costs hidden on the
 * dashboard, then typed and read line by line on the purchasing screen. The
 * backend already enforces owner/manager on `POST /purchases`, so the grant only
 * ever promised inventory staff a screen the server answers 403 to.
 * Receiving stock is separate and still theirs, via `inventory.adjust`.
 *
 * Purchase orders are a different document on purpose (`purchasing.orders.manage`):
 * writing down what to order is counter work every store role does, including
 * cashiers -- only converting an order into a cost-bearing purchase stays
 * manager+. Searching the unified catalogue (`catalogue.search`) is read-only and
 * open to everyone; acting on it (`products.adopt`, or the legacy writes under
 * `store.manage`) stops at owner/manager, matching the server.
 */
const matrix: Record<Role, readonly Capability[]> = {
  owner: ['organization.manage', 'store.manage', 'users.manage', 'sales.create', 'sales.refund', 'inventory.read', 'inventory.adjust', 'purchases.manage', 'purchasing.orders.manage', 'catalogue.search', 'products.adopt', 'reports.read', 'reports.read_costs'],
  manager: ['store.manage', 'users.manage', 'sales.create', 'sales.refund', 'inventory.read', 'inventory.adjust', 'purchases.manage', 'purchasing.orders.manage', 'catalogue.search', 'products.adopt', 'reports.read', 'reports.read_costs'],
  cashier: ['sales.create', 'inventory.read', 'purchasing.orders.manage', 'catalogue.search', 'reports.read'],
  inventory_staff: ['inventory.read', 'inventory.adjust', 'purchasing.orders.manage', 'catalogue.search', 'reports.read'],
};

export function can(role: Role, capability: Capability): boolean { return matrix[role]?.includes(capability) ?? false; }
export function capabilitiesFor(role: Role): readonly Capability[] { return matrix[role] ?? []; }

/** Route/action guard: same matrix as `can`, but throws the shared `FORBIDDEN`
 *  error so callers map it straight onto the API's error taxonomy. */
export function assertCan(role: Role, capability: Capability, message = `Role '${role}' may not perform '${capability}'`): void {
  if (!can(role, capability)) throw domainError('FORBIDDEN', message, { role, capability });
}

// --- store scope ------------------------------------------------------------

export type StoreScope = { storeId: string; memberships: readonly StoreMembership[] };

/** Active branch assignments for a user; inactive rows grant nothing. */
export function activeStoreIds(memberships: readonly StoreMembership[]): string[] {
  return memberships.filter((row) => row.status === 'active').map((row) => row.storeId);
}

/** Whether a user may operate on a branch: an active `store_users` row must exist. */
export function canAccessStore(storeId: string, memberships: readonly StoreMembership[]): boolean {
  return memberships.some((row) => row.storeId === storeId && row.status === 'active');
}

export function assertStoreAccess(storeId: string, memberships: readonly StoreMembership[]): void {
  if (!canAccessStore(storeId, memberships)) {
    throw domainError('FORBIDDEN', 'Store access denied', { storeId });
  }
}

// --- cost/profit restriction -------------------------------------------------

/** Rows that expose cost or profit; stripped for roles without `reports.read_costs`. */
export type CostBearing<T extends object = Record<string, unknown>> = T & { cost?: unknown; profit?: unknown };

/** Hide cost and profit from roles that only hold `reports.read` -- a cashier
 *  seeing unit economics is a data-leak, not a convenience. */
export function redactCosts<T extends CostBearing>(rows: readonly T[], role: Role): T[] {
  if (can(role, 'reports.read_costs')) return rows.map((row) => ({ ...row }));
  return rows.map(({ cost: _cost, profit: _profit, ...visible }) => visible) as unknown as T[];
}

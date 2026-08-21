import type { Role } from '@pharmacy/types';

export type Capability =
  | 'organization.manage' | 'store.manage' | 'users.manage' | 'sales.create'
  | 'sales.refund' | 'inventory.read' | 'inventory.adjust' | 'purchases.manage'
  | 'reports.read' | 'reports.read_costs';

const matrix: Record<Role, readonly Capability[]> = {
  owner: ['organization.manage', 'store.manage', 'users.manage', 'sales.create', 'sales.refund', 'inventory.read', 'inventory.adjust', 'purchases.manage', 'reports.read', 'reports.read_costs'],
  manager: ['store.manage', 'users.manage', 'sales.create', 'sales.refund', 'inventory.read', 'inventory.adjust', 'purchases.manage', 'reports.read', 'reports.read_costs'],
  cashier: ['sales.create', 'inventory.read', 'reports.read'],
  inventory_staff: ['inventory.read', 'inventory.adjust', 'purchases.manage', 'reports.read'],
};

export function can(role: Role, capability: Capability): boolean { return matrix[role]?.includes(capability) ?? false; }
export function capabilitiesFor(role: Role): readonly Capability[] { return matrix[role] ?? []; }

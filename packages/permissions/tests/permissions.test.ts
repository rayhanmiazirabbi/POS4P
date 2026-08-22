import { describe, expect, it } from 'vitest';
import { isDomainError } from '@pharmacy/core';
import type { Role, StoreMembership } from '@pharmacy/types';
import {
  activeStoreIds, assertCan, assertStoreAccess, can, canAccessStore, capabilitiesFor,
  redactCosts, type Capability,
} from '../src/index';

const ROLES: Role[] = ['owner', 'manager', 'cashier', 'inventory_staff'];
const CAPABILITIES: Capability[] = [
  'organization.manage', 'store.manage', 'users.manage', 'sales.create', 'sales.refund',
  'inventory.read', 'inventory.adjust', 'purchases.manage', 'reports.read', 'reports.read_costs',
];

/** The full role × capability expectation, pinned so a matrix edit is a deliberate act. */
const EXPECTED: Record<Role, readonly Capability[]> = {
  owner: ['organization.manage', 'store.manage', 'users.manage', 'sales.create', 'sales.refund', 'inventory.read', 'inventory.adjust', 'purchases.manage', 'reports.read', 'reports.read_costs'],
  manager: ['store.manage', 'users.manage', 'sales.create', 'sales.refund', 'inventory.read', 'inventory.adjust', 'purchases.manage', 'reports.read', 'reports.read_costs'],
  cashier: ['sales.create', 'inventory.read', 'reports.read'],
  inventory_staff: ['inventory.read', 'inventory.adjust', 'purchases.manage', 'reports.read'],
};

describe('role × capability matrix', () => {
  it('matches the pinned expectation for every role and capability', () => {
    for (const role of ROLES) {
      expect(capabilitiesFor(role)).toEqual(EXPECTED[role]);
      for (const capability of CAPABILITIES) {
        expect(can(role, capability), `${role}/${capability}`).toBe(EXPECTED[role].includes(capability));
      }
    }
  });

  it('keeps cost visibility out of front-line roles', () => {
    expect(can('cashier', 'reports.read_costs')).toBe(false);
    expect(can('inventory_staff', 'reports.read_costs')).toBe(false);
    expect(can('inventory_staff', 'sales.refund')).toBe(false);
  });
});

describe('assertCan', () => {
  it('passes silently when the matrix allows and throws FORBIDDEN when it does not', () => {
    expect(() => assertCan('cashier', 'sales.create')).not.toThrow();
    const thrown = (() => { try { assertCan('cashier', 'users.manage'); } catch (error) { return error; } })();
    expect(isDomainError(thrown)).toBe(true);
    expect(thrown).toMatchObject({ code: 'FORBIDDEN', details: { role: 'cashier', capability: 'users.manage' } });
  });
});

describe('store scope', () => {
  // UUID fields are branded strings, so the fixture rows are built with a cast.
  const memberships = [
    { userId: 'u1', storeId: 's1', role: 'cashier', status: 'active' },
    { userId: 'u1', storeId: 's2', role: 'cashier', status: 'inactive' },
  ] as unknown as StoreMembership[];

  it('counts only active assignments as access', () => {
    expect(activeStoreIds(memberships)).toEqual(['s1']);
    expect(canAccessStore('s1', memberships)).toBe(true);
    expect(canAccessStore('s2', memberships)).toBe(false);
    expect(canAccessStore('s3', memberships)).toBe(false);
    expect(() => assertStoreAccess('s2', memberships)).toThrow();
    expect(() => assertStoreAccess('s1', memberships)).not.toThrow();
  });
});

describe('redactCosts', () => {
  const rows = [
    { storeId: 's1', gross: '100.00', cost: '60.00', profit: '40.00' },
    { storeId: 's2', gross: '50.00', cost: '20.00' },
  ];

  it('keeps cost and profit for roles with reports.read_costs', () => {
    expect(redactCosts(rows, 'owner')).toEqual(rows);
    expect(redactCosts(rows, 'manager')[0]).toHaveProperty('profit', '40.00');
  });

  it('strips cost and profit for everyone else', () => {
    expect(redactCosts(rows, 'cashier')).toEqual([
      { storeId: 's1', gross: '100.00' },
      { storeId: 's2', gross: '50.00' },
    ]);
    expect(redactCosts(rows, 'inventory_staff')[0]).not.toHaveProperty('cost');
  });
});

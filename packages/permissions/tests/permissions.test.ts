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
  'inventory.read', 'inventory.adjust', 'purchases.manage', 'purchases.receive', 'purchasing.orders.manage',
  'catalogue.search', 'products.adopt', 'reports.read', 'reports.read_costs',
];

/** The full role × capability expectation, pinned so a matrix edit is a deliberate act. */
const EXPECTED: Record<Role, readonly Capability[]> = {
  owner: ['organization.manage', 'store.manage', 'users.manage', 'sales.create', 'sales.refund', 'inventory.read', 'inventory.adjust', 'purchases.manage', 'purchases.receive', 'purchasing.orders.manage', 'catalogue.search', 'products.adopt', 'reports.read', 'reports.read_costs'],
  manager: ['store.manage', 'users.manage', 'sales.create', 'sales.refund', 'inventory.read', 'inventory.adjust', 'purchases.manage', 'purchases.receive', 'purchasing.orders.manage', 'catalogue.search', 'products.adopt', 'reports.read', 'reports.read_costs'],
  cashier: ['sales.create', 'inventory.read', 'purchases.receive', 'purchasing.orders.manage', 'catalogue.search', 'reports.read'],
  inventory_staff: ['inventory.read', 'inventory.adjust', 'purchases.receive', 'purchasing.orders.manage', 'catalogue.search', 'reports.read'],
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

  it('never grants purchasing to a role that may not read costs', () => {
    // Entering a purchase is entering supplier unit costs, so the two grants have
    // to move together. `inventory_staff` used to hold `purchases.manage` while
    // being denied `reports.read_costs`: the same figures were hidden on the
    // dashboard and typed line by line on the purchasing screen. The backend
    // guards `POST /purchases` at owner/manager, so the grant was also a promise
    // of a screen the server answers 403 to.
    for (const role of ROLES) {
      if (can(role, 'purchases.manage')) expect(can(role, 'reports.read_costs'), role).toBe(true);
    }
  });

  it('splits order paperwork from cost-bearing purchases', () => {
    // Purchase orders are what the shop needs written down before goods arrive;
    // every store role writes them. Conversion into a purchase stays behind the
    // owner/manager `purchases.manage` grant on both server and matrix.
    for (const role of ROLES) {
      expect(can(role, 'purchasing.orders.manage'), `${role}/orders`).toBe(true);
      expect(can(role, 'catalogue.search'), `${role}/search`).toBe(true);
    }
    expect(can('cashier', 'products.adopt')).toBe(false);
    expect(can('inventory_staff', 'products.adopt')).toBe(false);
    expect(can('cashier', 'purchases.manage')).toBe(false);
  });

  it('leaves inventory_staff able to receive stock', () => {
    // Purchasing left, receiving stayed. Removing both would have made the role
    // unable to do the work it is named for.
    expect(can('inventory_staff', 'inventory.adjust')).toBe(true);
    expect(can('inventory_staff', 'purchases.manage')).toBe(false);
    for (const role of ROLES) expect(can(role, 'purchases.receive'), role).toBe(true);
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

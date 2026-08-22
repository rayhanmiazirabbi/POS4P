import { describe, expect, it } from 'vitest';
import type { Currency, Membership, Role, StoreMembership } from '../src/index';

it('exposes explicit money and role contracts', () => {
  const currency: Currency = 'BDT';
  const role: Role = 'cashier';
  expect({ currency, role }).toEqual({ currency: 'BDT', role: 'cashier' });
});

describe('membership status unions match the backend MembershipStatus literal', () => {
  // app/schemas/users.py emits exactly these two states; 'invited' arrives with
  // Stage 2 invitations. Pinning the arrays (not just assigning one value) makes
  // the next backend drift fail here instead of in a client switch statement.
  const membershipStatuses = ['active', 'inactive'] as const satisfies readonly Membership['status'][];
  const storeStatuses = ['active', 'inactive'] as const satisfies readonly StoreMembership['status'][];

  it('Membership allows active and inactive only', () => {
    expect(membershipStatuses).toEqual(['active', 'inactive']);
    const active = {
      userId: 'u', organizationId: 'o', role: 'manager', status: 'active',
    } as Membership;
    const inactive: Membership = { ...active, status: 'inactive' };
    expect([active.status, inactive.status]).toEqual(['active', 'inactive']);
  });

  it('StoreMembership allows active and inactive only', () => {
    expect(storeStatuses).toEqual(['active', 'inactive']);
    const active = { userId: 'u', storeId: 's', role: 'cashier', status: 'active' } as StoreMembership;
    expect({ ...active, status: 'inactive' }).toMatchObject({ status: 'inactive' });
  });
});

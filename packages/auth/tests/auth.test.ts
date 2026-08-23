import { describe, expect, it, vi } from 'vitest';
import { createMemoryStorage, storageKeys, type MembershipOption, type MembershipStore, type TokenBundle } from '@pharmacy/api';
import type { Session } from '@pharmacy/types';
import { contextChoice, SessionManager, storeChoice } from '../src/index';

const session: Session = { accessToken: 'access', refreshToken: 'refresh', expiresAt: '2026-01-01T00:00:00.000Z', user: {} } as Session;

function manager(storage = createMemoryStorage()) {
  return {
    storage,
    manager: new SessionManager({ storage, refresh: vi.fn(async () => session), logout: vi.fn(async () => undefined) }),
  };
}

describe('SessionManager', () => {
  it('persists and removes session credentials through the secure adapter', async () => {
    const { storage, manager: instance } = manager();
    await instance.persist(session);
    expect((await instance.restore())?.accessToken).toBe('access');
    await instance.logout();
    expect(await instance.restore()).toBeNull();
  });

  it('uses the storage keys owned by @pharmacy/api', async () => {
    const { storage, manager: instance } = manager();
    await instance.persist(session);
    expect(await storage.get(storageKeys.accessToken)).toBe('access');
    expect(await storage.get(storageKeys.refreshToken)).toBe('refresh');
  });

  it('keeps the refresh token across an app restart', async () => {
    const { storage, manager: first } = manager();
    await first.persist(session);

    // A fresh manager over the same storage models a cold start.
    const { manager: second } = manager(storage);
    const restored = await second.restore();
    expect(restored?.refreshToken).toBe('refresh');
    expect(await storage.get(storageKeys.refreshToken)).toBe('refresh');

    await second.logout();
    expect(await storage.get(storageKeys.refreshToken)).toBeNull();
    expect(await storage.get(storageKeys.session)).toBeNull();
  });

  it('clears corrupt storage rather than throwing', async () => {
    const { storage, manager: instance } = manager();
    await storage.set(storageKeys.session, '{not json');
    expect(await instance.restore()).toBeNull();
    expect(await storage.get(storageKeys.session)).toBeNull();
  });
});

const mirpur: MembershipStore = { id: 'store-1', code: 'MIR', name: 'Mirpur branch' };
const uttara: MembershipStore = { id: 'store-2', code: 'UTT', name: 'Uttara branch' };

function membership(overrides: Partial<MembershipOption> = {}): MembershipOption {
  return { organizationId: 'org-1', organizationName: 'Bismillah Pharmacy', role: 'owner', stores: [mirpur], ...overrides };
}

function bundle(overrides: Partial<TokenBundle> = {}): TokenBundle {
  return {
    requiresOrganization: false,
    organizationId: 'org-1',
    storeId: 'store-1',
    organizations: [membership()],
    ...overrides,
  } as TokenBundle;
}

describe('contextChoice', () => {
  it('sends a fully scoped token straight to the counter', () => {
    expect(contextChoice(bundle())).toEqual({ kind: 'ready' });
  });

  it('asks which branch when a tenant has more than one and none is pinned', () => {
    // The regression this whole type exists for. All three shells called
    // `selectContext(organizationId, storeIds[0])` here, so a two-branch account
    // was scoped to whichever branch came back first -- and the query behind that
    // list had no ORDER BY, so it was not even consistently the same one. The shelf
    // that then loaded looked perfectly normal.
    const choice = contextChoice(bundle({ storeId: null, organizations: [membership({ stores: [mirpur, uttara] })] }));
    expect(choice.kind).toBe('store');
    expect(choice.kind === 'store' && choice.options).toEqual([mirpur, uttara]);
  });

  it('resolves a single branch without asking', () => {
    // One branch is not a choice. Presenting it as one would put a pointless screen
    // in front of every cashier at every shift change.
    const choice = contextChoice(bundle({ storeId: null }));
    expect(choice).toEqual({ kind: 'select', organization: membership(), store: mirpur });
  });

  it('asks which tenant first when there are several', () => {
    const second = membership({ organizationId: 'org-2', organizationName: 'Shefa Medical', stores: [uttara] });
    const choice = contextChoice(bundle({ requiresOrganization: true, organizationId: null, storeId: null, organizations: [membership(), second] }));
    expect(choice.kind).toBe('organization');
    expect(choice.kind === 'organization' && choice.options).toHaveLength(2);
  });

  it('skips the tenant question when there is only one to ask about', () => {
    const choice = contextChoice(bundle({ requiresOrganization: true, organizationId: null, storeId: null }));
    expect(choice).toEqual({ kind: 'select', organization: membership(), store: mirpur });
  });

  it('reports a membership with no active branch rather than sending a blank one', () => {
    // Not answerable by picking. Sending `storeId: undefined` would scope a token
    // with no branch and every counter call would answer STORE_CONTEXT_REQUIRED --
    // true, but it does not tell anyone that the branch was suspended.
    const stranded = membership({ stores: [] });
    expect(contextChoice(bundle({ storeId: null, organizations: [stranded] }))).toEqual({ kind: 'stranded', organization: stranded });
  });

  it('reports a token that belongs to no organization yet as onboarding', () => {
    // The new-owner bootstrap: this token exists to authenticate
    // `POST /organizations` and nothing else. The shells used to sign it into the
    // POS, where every call answers STORE_CONTEXT_REQUIRED.
    expect(contextChoice(bundle({ requiresOrganization: true, organizationId: null, storeId: null, organizations: [] }))).toEqual({ kind: 'onboarding' });
  });

  it('does not second-guess a token scoped to a tenant the list omits', () => {
    // Whatever produced this, inventing a branch for it would be worse.
    expect(contextChoice(bundle({ organizationId: 'org-9', storeId: null }))).toEqual({ kind: 'ready' });
  });
});

describe('storeChoice', () => {
  it('is what the organization picker hands its answer to', () => {
    const multi = membership({ stores: [mirpur, uttara] });
    expect(storeChoice(multi)).toEqual({ kind: 'store', organization: multi, options: [mirpur, uttara] });
    expect(storeChoice(membership())).toEqual({ kind: 'select', organization: membership(), store: mirpur });
    expect(storeChoice(membership({ stores: [] })).kind).toBe('stranded');
  });
});

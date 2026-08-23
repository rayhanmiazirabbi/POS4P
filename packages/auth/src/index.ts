import { storageKeys, type MembershipOption, type MembershipStore, type StorageAdapter, type TokenBundle } from '@pharmacy/api';
import type { Session } from '@pharmacy/types';

export type AuthState = { session: Session | null; deviceId: string | null };
export type AuthAdapter = { storage: StorageAdapter; refresh(): Promise<Session>; logout(): Promise<void> };

/**
 * What a login screen still has to settle before a session can be scoped.
 *
 * Six cases because each is a different screen, and collapsing any two of them is
 * how the shells got this wrong. `select` in particular is not `store` with one
 * option: one branch is not a choice and must not be presented as one, while two
 * branches are a choice and must not be resolved as if they were not.
 */
export type ContextChoice =
  /** The server already scoped the token. Go to the counter. */
  | { kind: 'ready' }
  /** Nothing to ask: call `selectContext` with this pair and carry on. */
  | { kind: 'select'; organization: MembershipOption; store: MembershipStore }
  /** Several tenants. Ask, then hand the answer to `storeChoice`. */
  | { kind: 'organization'; options: readonly MembershipOption[] }
  /** Several branches in a known tenant. Ask. */
  | { kind: 'store'; organization: MembershipOption; options: readonly MembershipStore[] }
  /** A membership with no active branch. Not answerable by picking; say so. */
  | { kind: 'stranded'; organization: MembershipOption }
  /** Authenticated but a member of nothing yet -- the new-owner bootstrap. */
  | { kind: 'onboarding' };

/**
 * Read a token bundle and say what remains to be settled.
 *
 * This exists because all three shells answered it themselves, identically and
 * wrongly: each called `selectContext(organizationId, storeIds[0])`, taking the
 * first branch of a multi-branch account without asking. The token was then pinned
 * to a branch nobody chose, so `/products/current` returned that branch's shelf and
 * every sale drew down its stock -- quietly, because a plausible shelf appeared and
 * the counter had no way to tell it was the wrong one.
 *
 * Note the asymmetry with the organization step. The server volunteers
 * `requiresOrganization`, but there is no `requiresStore`, because a null `storeId`
 * is legitimate for an owner acting organization-wide. So the store question is
 * asked here instead, from the shape of the membership.
 */
export function contextChoice(bundle: TokenBundle): ContextChoice {
  // No membership at all: a brand-new owner whose token exists only to authenticate
  // `POST /organizations`. Signing such a token into a POS shell -- which is what
  // the shells did -- lands on a counter where every call answers
  // STORE_CONTEXT_REQUIRED.
  if (bundle.organizations.length === 0) return { kind: 'onboarding' };
  if (bundle.organizationId != null && bundle.storeId != null) return { kind: 'ready' };
  if (bundle.organizationId != null) {
    const scoped = bundle.organizations.find((option) => option.organizationId === bundle.organizationId);
    // Scoped to a tenant the list does not mention: not ours to second-guess.
    return scoped === undefined ? { kind: 'ready' } : storeChoice(scoped);
  }
  const sole = bundle.organizations.length === 1 ? bundle.organizations[0] : undefined;
  return sole === undefined ? { kind: 'organization', options: bundle.organizations } : storeChoice(sole);
}

/** The branch step for one tenant: resolve it, ask about it, or report that there
 *  is nothing to resolve. Call this with whatever the organization picker returns. */
export function storeChoice(organization: MembershipOption): ContextChoice {
  const sole = organization.stores.length === 1 ? organization.stores[0] : undefined;
  if (sole !== undefined) return { kind: 'select', organization, store: sole };
  if (organization.stores.length === 0) return { kind: 'stranded', organization };
  return { kind: 'store', organization, options: organization.stores };
}

export class SessionManager {
  constructor(private readonly adapter: AuthAdapter) {}

  async restore(): Promise<Session | null> {
    const serialized = await this.adapter.storage.get(storageKeys.session);
    if (!serialized) return null;
    try { return JSON.parse(serialized) as Session; } catch { await this.clear(); return null; }
  }

  async persist(session: Session): Promise<void> {
    await this.adapter.storage.set(storageKeys.accessToken, session.accessToken);
    // The refresh token must survive the process: without it an app restart
    // cannot rotate credentials and the user is dumped back at the OTP screen.
    await this.adapter.storage.set(storageKeys.refreshToken, session.refreshToken);
    await this.adapter.storage.set(storageKeys.session, JSON.stringify(session));
  }

  async refresh(): Promise<Session> { const session = await this.adapter.refresh(); await this.persist(session); return session; }
  async logout(): Promise<void> { await this.adapter.logout(); await this.clear(); }

  private async clear(): Promise<void> {
    await this.adapter.storage.remove(storageKeys.accessToken);
    await this.adapter.storage.remove(storageKeys.refreshToken);
    await this.adapter.storage.remove(storageKeys.session);
  }
}

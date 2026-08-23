'use client';

import type { MembershipOption, MembershipStore, OtpChallenge, TokenBundle } from '@pharmacy/api';
import type { ApiResponse } from '@pharmacy/types';
import { contextChoice, storeChoice, type ContextChoice } from '@pharmacy/auth';
import { colors, spacing, tokens } from '@pharmacy/design-tokens';
import { useRouter } from 'next/navigation';
import { useState, type CSSProperties, type FormEvent, type ReactNode } from 'react';

import { deviceIdentity, deviceName, pharmacyApi } from '@/lib/api';
import { fieldIssue, phoneNumber } from '@/lib/validation';
import { useSession } from '@/lib/session';

const inputStyle: CSSProperties = {
  width: '100%',
  padding: spacing.md,
  borderRadius: 8,
  border: `1px solid ${colors.border}`,
  fontSize: tokens.typography.sizes.md,
  marginBottom: spacing.md,
  boxSizing: 'border-box',
};

const buttonStyle: CSSProperties = {
  width: '100%',
  padding: spacing.md,
  borderRadius: 8,
  border: 'none',
  background: colors.primary,
  color: colors.primaryForeground,
  fontWeight: tokens.typography.weights.semibold,
  fontSize: tokens.typography.sizes.md,
  cursor: 'pointer',
};

/** One pickable row: a tenant, or a branch inside one. */
const choiceStyle: CSSProperties = {
  ...buttonStyle,
  background: colors.background,
  color: colors.foreground,
  border: `1px solid ${colors.border}`,
  marginBottom: spacing.sm,
  textAlign: 'left',
};

type Stage = 'phone' | 'code' | 'workspace' | 'branch' | 'stranded' | 'onboarding';

/** Checked before the OTP is spent: the SMS is the one part of this flow that
 *  costs money, and it goes to whatever number is in the box. */
function phoneIssue(phone: string): string | null {
  return fieldIssue(phoneNumber.safeParse(phone));
}

export default function LoginPage(): ReactNode {
  const router = useRouter();
  const { signIn } = useSession();
  const [stage, setStage] = useState<Stage>('phone');
  const [phone, setPhone] = useState('+8801700000001');
  const [code, setCode] = useState('');
  const [challenge, setChallenge] = useState<OtpChallenge | null>(null);
  const [workspaces, setWorkspaces] = useState<readonly MembershipOption[]>([]);
  const [organization, setOrganization] = useState<MembershipOption | null>(null);
  const [branches, setBranches] = useState<readonly MembershipStore[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const phoneValid = phoneIssue(phone) === null;
  const invalidPhone = phoneValid ? null : phoneIssue(phone);

  /**
   * Act on what `@pharmacy/auth` says is still unsettled.
   *
   * The branch question used to be answered here by taking `storeIds[0]`, which
   * scoped the session to whichever branch the server listed first. Every sale and
   * every stock figure after that belonged to that branch, and nothing said so.
   */
  async function resolve(choice: ContextChoice): Promise<void> {
    switch (choice.kind) {
      case 'ready':
        router.replace('/pos');
        return;
      case 'select':
        await scope(choice.organization.organizationId, choice.store.id);
        return;
      case 'organization':
        setWorkspaces(choice.options);
        setStage('workspace');
        return;
      case 'store':
        setOrganization(choice.organization);
        setBranches(choice.options);
        setStage('branch');
        return;
      case 'stranded':
        setOrganization(choice.organization);
        setStage('stranded');
        return;
      case 'onboarding':
        setStage('onboarding');
        return;
    }
  }

  async function requestCode(event: FormEvent): Promise<void> {
    event.preventDefault();
    if (!phoneValid) return;
    setBusy(true);
    setError(null);
    try {
      const response = await pharmacyApi.auth.requestOtp({ phone, purpose: 'login' });
      setChallenge(response.data);
      // Development deliveries include the code so staff can sign in without SMS.
      if (response.data.devCode) setCode(response.data.devCode);
      setStage('code');
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Could not send the code');
    } finally {
      setBusy(false);
    }
  }

  async function verifyCode(event: FormEvent): Promise<void> {
    event.preventDefault();
    if (challenge === null) return;
    setBusy(true);
    setError(null);
    try {
      // The device claim rides along on every login. It is what puts the `dev`
      // claim on the token, and without that `/sync/events` refuses the offline
      // queue outright -- so a terminal that signs in without it can ring up
      // sales it can never upload.
      const response = await pharmacyApi.auth.verifyOtp({
        challengeId: challenge.challengeId,
        code,
        device: await deviceIdentity.claim(deviceName()),
      });
      const bundle = response.data;
      const choice = contextChoice(bundle);
      // Only a token the server already scoped is worth storing. Signing in on an
      // unscoped one and then asking about the branch would leave the POS reachable
      // in between, pointed at nothing.
      if (choice.kind === 'ready') await signIn(bundle);
      await resolve(choice);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Verification failed');
    } finally {
      setBusy(false);
    }
  }

  /** Re-scope onto a chosen tenant and branch, then hand over to the shell. */
  async function scope(organizationId: string, storeId?: string): Promise<void> {
    setBusy(true);
    setError(null);
    try {
      // Claimed here too, not only on `verifyOtp`. The backend can bind a device
      // only once it knows the store, so on a multi-workspace account the verify
      // step resolves no device at all and this is the only step that can.
      const device = await deviceIdentity.claim(deviceName());
      const response: ApiResponse<TokenBundle> = await pharmacyApi.auth.selectContext(
        storeId === undefined ? { organizationId, device } : { organizationId, storeId, device },
      );
      await signIn(response.data);
      router.replace('/pos');
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Could not select the workspace');
    } finally {
      setBusy(false);
    }
  }

  return (
    <main style={{ background: colors.background, color: colors.foreground, minHeight: '100vh', display: 'grid', placeItems: 'center', padding: spacing['2xl'] }}>
      <form
        onSubmit={stage === 'phone' ? requestCode : stage === 'code' ? verifyCode : undefined}
        style={{ background: colors.surface, padding: spacing['2xl'], borderRadius: 12, border: `1px solid ${colors.border}`, width: '100%', maxWidth: 380 }}
      >
        <h1 style={{ marginTop: 0, fontSize: tokens.typography.sizes.xl }}>{stage === 'branch' ? 'Which branch?' : 'Sign in'}</h1>
        {(stage === 'phone' || stage === 'code') && <p style={{ color: colors.muted, marginTop: 0 }}>{stage === 'phone' ? 'Staff phone number; a one-time code will be sent.' : 'Enter the code you received.'}</p>}

        {stage === 'phone' && (
          <>
            <label style={{ display: 'block', marginBottom: spacing.xs, fontWeight: tokens.typography.weights.medium }} htmlFor="phone">Phone</label>
            <input id="phone" style={inputStyle} value={phone} onChange={(event) => setPhone(event.target.value)} inputMode="tel" autoComplete="tel" aria-invalid={!phoneValid} />
            {invalidPhone !== null && (
              <p role="alert" style={{ marginTop: 0, color: colors.danger, fontSize: tokens.typography.sizes.sm }}>{invalidPhone}</p>
            )}
            <button type="submit" disabled={busy || !phoneValid} style={{ ...buttonStyle, opacity: phoneValid ? 1 : 0.6 }}>{busy ? 'Sending…' : 'Send code'}</button>
          </>
        )}

        {stage === 'code' && (
          <>
            <label style={{ display: 'block', marginBottom: spacing.xs, fontWeight: tokens.typography.weights.medium }} htmlFor="code">Code</label>
            <input id="code" style={inputStyle} value={code} onChange={(event) => setCode(event.target.value)} inputMode="numeric" autoFocus />
            <button type="submit" disabled={busy || code.length === 0} style={buttonStyle}>{busy ? 'Verifying…' : 'Verify'}</button>
          </>
        )}

        {stage === 'workspace' && (
          <>
            <p style={{ color: colors.muted, marginTop: 0 }}>Choose a workspace.</p>
            {workspaces.map((option) => (
              <button
                key={option.organizationId}
                type="button"
                disabled={busy}
                // Straight to `storeChoice`, not to a session: a tenant with several
                // branches has a second question, and one with a single branch has
                // none. Picking either way here is what went wrong before.
                onClick={() => void resolve(storeChoice(option))}
                style={choiceStyle}
              >
                {option.organizationName}
                {option.stores.length > 1 ? ` — ${option.stores.length} branches` : ''}
              </button>
            ))}
          </>
        )}

        {stage === 'branch' && organization !== null && (
          <>
            <p style={{ color: colors.muted, marginTop: 0 }}>
              {organization.organizationName} — sales, stock and reports all belong to the branch you pick.
            </p>
            {branches.map((store) => (
              <button key={store.id} type="button" disabled={busy} onClick={() => void scope(organization.organizationId, store.id)} style={choiceStyle}>
                {store.name}
                <span style={{ color: colors.muted }}> · {store.code}</span>
              </button>
            ))}
          </>
        )}

        {stage === 'stranded' && organization !== null && (
          // A membership with no active branch. Not something a picker can fix, and
          // signing in anyway would produce a shell where every call fails.
          <p role="alert" style={{ color: colors.danger, marginTop: 0 }}>
            You are a member of {organization.organizationName}, but none of its branches are open to you right now. Ask an owner or
            manager to assign you to a branch, then sign in again.
          </p>
        )}

        {stage === 'onboarding' && (
          <p style={{ color: colors.muted, marginTop: 0 }}>
            This number is not a member of any pharmacy yet. Create one to continue — until then there is no branch to sell from or
            report on.
          </p>
        )}

        {error !== null && <p role="alert" style={{ color: colors.danger, marginBottom: 0 }}>{error}</p>}
      </form>
    </main>
  );
}

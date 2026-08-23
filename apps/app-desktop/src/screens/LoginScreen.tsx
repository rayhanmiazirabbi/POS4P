import type { MembershipOption, MembershipStore, OtpChallenge, TokenBundle } from '@pharmacy/api';
import type { ApiResponse } from '@pharmacy/types';
import { contextChoice, storeChoice, type ContextChoice } from '@pharmacy/auth';
import { colors, spacing, tokens } from '@pharmacy/design-tokens';
import { useEffect, useState, type CSSProperties, type KeyboardEvent, type ReactNode } from 'react';

import { deviceIdentity, deviceName, pharmacyApi } from '../lib/api';
import { useSession } from '../lib/session';
import { forgetTerminal, readTerminal, type TerminalBinding } from '../lib/terminal';

const input: CSSProperties = { width: '100%', padding: spacing.md, borderRadius: 8, border: `1px solid ${colors.border}`, marginBottom: spacing.md, boxSizing: 'border-box' };
const button: CSSProperties = { width: '100%', padding: spacing.md, borderRadius: 8, border: 'none', background: colors.primary, color: colors.primaryForeground, fontWeight: tokens.typography.weights.semibold, cursor: 'pointer' };
const linkButton: CSSProperties = { ...button, background: 'transparent', color: colors.muted, fontWeight: tokens.typography.weights.regular, marginBottom: 0 };
/** One pickable row: a shop, or a branch inside one. */
const choice: CSSProperties = { ...button, background: colors.background, color: colors.foreground, border: `1px solid ${colors.border}`, marginBottom: spacing.sm, textAlign: 'left' };

type Stage = 'pin' | 'phone' | 'code' | 'workspace' | 'branch' | 'stranded' | 'onboarding';

export function LoginScreen(): ReactNode {
  const { signIn } = useSession();
  const [terminal, setTerminal] = useState<TerminalBinding | null>(null);
  const [stage, setStage] = useState<Stage>('phone');
  const [phone, setPhone] = useState('+8801700000001');
  const [pin, setPin] = useState('');
  const [code, setCode] = useState('');
  const [challenge, setChallenge] = useState<OtpChallenge | null>(null);
  const [workspaces, setWorkspaces] = useState<readonly MembershipOption[]>([]);
  const [organization, setOrganization] = useState<MembershipOption | null>(null);
  const [branches, setBranches] = useState<readonly MembershipStore[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    // A till that has been signed into before opens on PIN entry, which is the
    // shift-change case and by far the common one. First run has no binding and
    // no PIN to check against, so it opens on the SMS path instead.
    void readTerminal().then((binding) => {
      setTerminal(binding);
      if (binding !== null) setStage('pin');
    });
  }, []);

  async function unlockWithPin(): Promise<void> {
    if (terminal === null) return;
    setBusy(true);
    setError(null);
    try {
      // The device claim rides along here for the same reason it does on the SMS
      // path: without it the token carries no `dev` claim, `/sync/events` answers
      // DEVICE_CONTEXT_REQUIRED, and the till can ring up sales it cannot upload.
      const response: ApiResponse<TokenBundle> = await pharmacyApi.auth.loginWithPin({
        phone,
        pin,
        organizationId: terminal.organizationId,
        ...(terminal.storeId === null ? {} : { storeId: terminal.storeId }),
        device: await deviceIdentity.claim(deviceName()),
      });
      await signIn(response.data);
    } catch (cause) {
      // The backend answers every PIN failure with one message and one shape on
      // purpose, so there is nothing to elaborate here. `RATE_LIMITED` is the
      // exception worth naming: the account is locked for a while and repeating
      // the attempt cannot help, so say so rather than invite another try.
      const code_ = (cause as { code?: string }).code;
      setError(
        code_ === 'RATE_LIMITED'
          ? 'Too many incorrect PINs. This account is locked for a few minutes — use the phone code instead.'
          : cause instanceof Error
            ? cause.message
            : 'Unlock failed',
      );
      setPin('');
    } finally {
      setBusy(false);
    }
  }

  async function requestCode(): Promise<void> {
    setBusy(true);
    setError(null);
    try {
      const response: ApiResponse<OtpChallenge> = await pharmacyApi.auth.requestOtp({ phone, purpose: 'login' });
      setChallenge(response.data);
      if (response.data.devCode) setCode(response.data.devCode);
      setStage('code');
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Could not send the code');
    } finally {
      setBusy(false);
    }
  }

  async function verify(): Promise<void> {
    if (challenge === null) return;
    setBusy(true);
    setError(null);
    try {
      // The device claim rides along on every login. It is what puts the `dev`
      // claim on the token, and without that `/sync/events` refuses the offline
      // queue outright -- so a till that signs in without it can ring up sales it
      // can never upload.
      const response: ApiResponse<TokenBundle> = await pharmacyApi.auth.verifyOtp({
        challengeId: challenge.challengeId,
        code,
        device: await deviceIdentity.claim(deviceName()),
      });
      const bundle = response.data;
      const choice = contextChoice(bundle);
      // Only a token the server already scoped is worth storing. Signing in on an
      // unscoped one and then asking about the branch would leave the till
      // reachable in between, pointed at nothing.
      if (choice.kind === 'ready') await signIn(bundle);
      await resolve(choice);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Verification failed');
    } finally {
      setBusy(false);
    }
  }

  /**
   * Act on what `@pharmacy/auth` says is still unsettled.
   *
   * This used to be `chooseWorkspace(option.organizationId, option.storeIds[0])`,
   * which bound the till to whichever branch the server listed first. On a
   * multi-branch shop that is the wrong shelf, the wrong stock, and -- because
   * `rememberTerminal` records what `me()` reports -- a PIN unlock that keeps
   * returning to it every shift afterwards.
   */
  async function resolve(choice: ContextChoice): Promise<void> {
    switch (choice.kind) {
      case 'ready':
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

  /** Re-scope onto a chosen tenant and branch. `SessionProvider` takes it from here. */
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
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Could not select the workspace');
    } finally {
      setBusy(false);
    }
  }

  /** Unbind the till from its shop and fall back to the SMS path. Offered because a
   *  machine does get moved between branches, and a stale binding would keep
   *  pinning PIN unlock to the branch it left. */
  async function useAnotherShop(): Promise<void> {
    await forgetTerminal();
    setTerminal(null);
    setPin('');
    setError(null);
    setStage('phone');
  }

  function onEnter(event: KeyboardEvent, action: () => void): void {
    if (event.key === 'Enter') action();
  }

  return (
    <main style={{ background: colors.background, color: colors.foreground, minHeight: '100vh', display: 'grid', placeItems: 'center', fontFamily: tokens.typography.family }}>
      <div style={{ background: colors.surface, padding: spacing['2xl'], borderRadius: 12, border: `1px solid ${colors.border}`, width: 360 }}>
        <h1 style={{ marginTop: 0, fontSize: tokens.typography.sizes.xl }}>
          {stage === 'pin' ? 'Unlock till' : stage === 'branch' ? 'Which branch?' : 'Counter sign-in'}
        </h1>
        {stage === 'pin' && terminal !== null && (
          <>
            <p style={{ marginTop: 0, color: colors.muted, fontSize: tokens.typography.sizes.sm }}>
              {terminal.organizationName}
              {terminal.storeName === null ? '' : ` · ${terminal.storeName}`}
            </p>
            <input style={input} value={phone} onChange={(event) => setPhone(event.target.value)} placeholder="Phone" onKeyDown={(event) => onEnter(event, () => void unlockWithPin())} />
            <input
              style={input}
              value={pin}
              onChange={(event) => setPin(event.target.value)}
              placeholder="PIN"
              type="password"
              inputMode="numeric"
              autoFocus
              onKeyDown={(event) => onEnter(event, () => void unlockWithPin())}
            />
            <button type="button" style={button} disabled={busy || pin.length === 0} onClick={() => void unlockWithPin()}>Unlock</button>
            {/* No PIN set yet, or forgotten: the SMS path always works and issuing a
                PIN is an owner/manager action on another screen, so the way out has
                to be here rather than a dead end. */}
            <button type="button" style={linkButton} disabled={busy} onClick={() => setStage('phone')}>Use a phone code instead</button>
            <button type="button" style={linkButton} disabled={busy} onClick={() => void useAnotherShop()}>This till belongs to another shop</button>
          </>
        )}
        {stage === 'phone' && (
          <>
            <input style={input} value={phone} onChange={(event) => setPhone(event.target.value)} placeholder="Phone" autoFocus onKeyDown={(event) => onEnter(event, () => void requestCode())} />
            <button type="button" style={button} disabled={busy} onClick={() => void requestCode()}>Send code</button>
            {terminal !== null && <button type="button" style={linkButton} disabled={busy} onClick={() => setStage('pin')}>Back to PIN unlock</button>}
          </>
        )}
        {stage === 'code' && (
          <>
            <input style={input} value={code} onChange={(event) => setCode(event.target.value)} placeholder="Code" autoFocus onKeyDown={(event) => onEnter(event, () => void verify())} />
            <button type="button" style={button} disabled={busy || code.length === 0} onClick={() => void verify()}>Verify</button>
          </>
        )}
        {stage === 'workspace' &&
          workspaces.map((option) => (
            // Straight to `storeChoice`, not to a session: a shop with several
            // branches has a second question, and one with a single branch has
            // none. Picking either way here is what went wrong before.
            <button key={option.organizationId} type="button" style={choice} disabled={busy} onClick={() => void resolve(storeChoice(option))}>
              {option.organizationName}
              {option.stores.length > 1 && <span style={{ color: colors.muted }}> · {option.stores.length} branches</span>}
            </button>
          ))}
        {stage === 'branch' && organization !== null && (
          <>
            <p style={{ marginTop: 0, color: colors.muted, fontSize: tokens.typography.sizes.sm }}>
              {organization.organizationName} — this till will be bound to the branch you pick, and PIN unlock will return to it.
            </p>
            {branches.map((store) => (
              <button key={store.id} type="button" style={choice} disabled={busy} onClick={() => void scope(organization.organizationId, store.id)}>
                {store.name}
                <span style={{ color: colors.muted }}> · {store.code}</span>
              </button>
            ))}
          </>
        )}
        {stage === 'stranded' && organization !== null && (
          // A membership with no active branch. Not something a picker can fix, and
          // signing in anyway would produce a till that refuses every sale.
          <p role="alert" style={{ color: colors.danger }}>
            You are a member of {organization.organizationName}, but none of its branches are open to you right now. Ask an owner or
            manager to assign you to a branch, then sign in again.
          </p>
        )}
        {stage === 'onboarding' && (
          <p style={{ color: colors.muted }}>
            This number is not a member of any pharmacy yet. Set one up in the web app — a till needs a branch to sell from.
          </p>
        )}
        {error !== null && <p role="alert" style={{ color: colors.danger }}>{error}</p>}
      </div>
    </main>
  );
}

import type { MembershipOption, MembershipStore, OtpChallenge, TokenBundle } from '@pharmacy/api';
import type { ApiResponse } from '@pharmacy/types';
import { contextChoice, storeChoice, type ContextChoice } from '@pharmacy/auth';
import { colors } from '@pharmacy/design-tokens';
import { router } from 'expo-router';
import { useState, type ReactNode } from 'react';
import { ActivityIndicator, Button, Pressable, Text, TextInput, View } from 'react-native';

import { pharmacyApi, deviceIdentity, deviceName } from '../../src/lib/api';
import { useSession } from '../../src/lib/session';

type Stage = 'phone' | 'code' | 'workspace' | 'branch' | 'stranded' | 'onboarding';

export default function LoginScreen(): ReactNode {
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

  /**
   * Act on what `@pharmacy/auth` says is still unsettled.
   *
   * The branch question used to be answered here by taking `storeIds[0]`, which
   * scoped the phone to whichever branch the server happened to list first. Every
   * sale after that drew down that branch's stock, and nothing on screen said so.
   */
  async function resolve(choice: ContextChoice): Promise<void> {
    switch (choice.kind) {
      case 'ready':
        router.replace('/(pos)/pos');
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

  async function verifyCode(): Promise<void> {
    if (challenge === null) return;
    setBusy(true);
    setError(null);
    try {
      // The device claim rides along on every login. It is what gives the token
      // its `dev` claim, and without that `/sync/events` refuses the offline
      // queue outright -- so a phone that logs in without it can ring up sales
      // it can never upload.
      const response: ApiResponse<TokenBundle> = await pharmacyApi.auth.verifyOtp({
        challengeId: challenge.challengeId,
        code,
        device: await deviceIdentity.claim(deviceName()),
      });
      const bundle = response.data;
      const choice = contextChoice(bundle);
      // Only a token the server already scoped is worth storing. Signing in on an
      // unscoped one and then asking about the branch would leave the counter
      // reachable in between, pointed at nothing.
      if (choice.kind === 'ready') await signIn(bundle);
      await resolve(choice);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Verification failed');
    } finally {
      setBusy(false);
    }
  }

  /** Re-scope onto a chosen tenant and branch, then hand over to the counter. */
  async function scope(organizationId: string, storeId?: string): Promise<void> {
    setBusy(true);
    setError(null);
    try {
      // Also claimed here, not just on `verifyOtp`. The backend can only bind a
      // device once it knows the store, so on a multi-workspace account the
      // verify step resolves no device at all and this is the only step that can.
      const device = await deviceIdentity.claim(deviceName());
      const response: ApiResponse<TokenBundle> = await pharmacyApi.auth.selectContext(
        storeId === undefined ? { organizationId, device } : { organizationId, storeId, device },
      );
      await signIn(response.data);
      router.replace('/(pos)/pos');
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Could not select the workspace');
    } finally {
      setBusy(false);
    }
  }

  return (
    <View style={{ flex: 1, padding: 24, gap: 12, backgroundColor: '#F8FAFC' }}>
      <Text style={{ fontSize: 24, fontWeight: '700', color: '#172033' }}>
        {stage === 'branch' ? 'Which branch?' : 'Sign in'}
      </Text>
      {stage === 'phone' && (
        <>
          <Text style={{ color: '#64748B' }}>Staff phone number; a one-time code will be sent.</Text>
          <TextInput value={phone} onChangeText={setPhone} inputMode="tel" autoCapitalize="none" style={input} />
          {busy ? <ActivityIndicator /> : <Button title="Send code" onPress={() => void requestCode()} />}
        </>
      )}
      {stage === 'code' && (
        <>
          <Text style={{ color: '#64748B' }}>Enter the code you received.</Text>
          <TextInput value={code} onChangeText={setCode} inputMode="numeric" autoFocus style={input} />
          {busy ? <ActivityIndicator /> : <Button title="Verify" onPress={() => void verifyCode()} disabled={code.length === 0} />}
        </>
      )}
      {stage === 'workspace' && (
        <>
          <Text style={{ color: '#64748B' }}>Choose a workspace.</Text>
          {workspaces.map((option) => (
            <Pressable
              key={option.organizationId}
              disabled={busy}
              // Straight to `storeChoice`, not to a session: a tenant with several
              // branches has a second question, and one with a single branch has
              // none. Picking either way here is what went wrong before.
              onPress={() => void resolve(storeChoice(option))}
              style={card}
            >
              <Text>{option.organizationName}</Text>
              {option.stores.length > 1 && <Text style={{ color: '#64748B' }}>{option.stores.length} branches</Text>}
            </Pressable>
          ))}
        </>
      )}
      {stage === 'branch' && organization !== null && (
        <>
          <Text style={{ color: '#64748B' }}>
            {organization.organizationName} — sales and stock are recorded against the branch you pick.
          </Text>
          {branches.map((store) => (
            <Pressable key={store.id} disabled={busy} onPress={() => void scope(organization.organizationId, store.id)} style={card}>
              <Text>{store.name}</Text>
              <Text style={{ color: '#64748B' }}>{store.code}</Text>
            </Pressable>
          ))}
          {busy && <ActivityIndicator />}
        </>
      )}
      {stage === 'stranded' && organization !== null && (
        // A membership with no active branch. Not something a picker can fix, and
        // signing in anyway would produce a counter where every action fails.
        <Text style={{ color: colors.danger }}>
          You are a member of {organization.organizationName}, but none of its branches are open to you right now. Ask an owner or
          manager to assign you to a branch, then sign in again.
        </Text>
      )}
      {stage === 'onboarding' && (
        <Text style={{ color: '#64748B' }}>
          This number is not a member of any pharmacy yet. Create one in the web app first — the phone app is the counter, and a
          counter needs a branch to sell from.
        </Text>
      )}
      {error !== null && <Text style={{ color: colors.danger }}>{error}</Text>}
    </View>
  );
}

const input = {
  padding: 12,
  borderRadius: 8,
  borderWidth: 1,
  borderColor: '#CBD5E1',
  backgroundColor: '#FFFFFF',
  color: '#172033',
} as const;

const card = {
  padding: 16,
  borderRadius: 8,
  borderWidth: 1,
  borderColor: '#CBD5E1',
  backgroundColor: '#FFFFFF',
} as const;

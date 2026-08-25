import { colors, spacing } from '@pharmacy/design-tokens';
import { can, type Capability } from '@pharmacy/permissions';
import { router } from 'expo-router';
import { useEffect, type ReactNode } from 'react';
import { ActivityIndicator, Pressable, Text, View } from 'react-native';

import { useSession } from './session';

/**
 * Withhold a counter screen from a role that may not use it.
 *
 * Sign-in cannot know the role -- it arrives with `me()` -- so every session
 * reaches these screens and the check has to happen on mount. Inventory staff
 * hold no `sales.create`: without this they got a working-looking till whose
 * every sale the server refuses, and offline that is worse, because the sale
 * queues durably in the outbox first and only fails on flush, one cart at a
 * time.
 *
 * `children` is an element, so a denied role never mounts the screen and never
 * runs its loaders -- this is a withheld screen, not a hidden link. Backend
 * enforcement stays mandatory either way; this only stops the client asking
 * questions it has no right to ask.
 */
export function RequireCapability({
  capability,
  children,
}: {
  capability: Capability;
  children: ReactNode;
}): ReactNode {
  const { status, user, signOut } = useSession();

  useEffect(() => {
    if (status === 'signed-out') router.replace('/(auth)/login');
  }, [status]);

  if (status !== 'signed-in' || user === null) {
    return (
      <View style={{ flex: 1, alignItems: 'center', justifyContent: 'center', backgroundColor: colors.background }}>
        <ActivityIndicator size="large" />
      </View>
    );
  }

  if (!can(user.role, capability)) {
    return <Denied role={user.role} onSignOut={() => void signOut()} />;
  }

  return children;
}

/** Said up front, with the way back out: on a shared handset the likely cause is
 *  the wrong person's sign-in, not a misassigned role. */
function Denied({ role, onSignOut }: { role: string; onSignOut: () => void }): ReactNode {
  return (
    <View
      style={{
        flex: 1,
        alignItems: 'center',
        justifyContent: 'center',
        padding: spacing['2xl'],
        gap: spacing.lg,
        backgroundColor: colors.background,
      }}
    >
      <Text style={{ fontSize: 18, fontWeight: '600', color: colors.foreground, textAlign: 'center' }}>
        This counter is for cashiers
      </Text>
      <Text accessibilityRole="alert" style={{ color: colors.muted, textAlign: 'center' }}>
        Your role ({role}) cannot record sales. Sign in as a cashier, or use the web app for stock and reporting work.
      </Text>
      <Pressable
        accessibilityRole="button"
        onPress={onSignOut}
        style={{
          paddingVertical: spacing.md,
          paddingHorizontal: spacing.xl,
          borderRadius: 8,
          backgroundColor: colors.primary,
        }}
      >
        <Text style={{ color: colors.primaryForeground, fontWeight: '600' }}>Sign out</Text>
      </Pressable>
    </View>
  );
}

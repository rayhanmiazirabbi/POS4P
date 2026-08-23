import { router } from 'expo-router';
import { useEffect, type ReactNode } from 'react';
import { ActivityIndicator, View } from 'react-native';

import { useSession } from '../src/lib/session';

export default function HomeScreen(): ReactNode {
  const { status } = useSession();

  useEffect(() => {
    if (status === 'signed-in') router.replace('/(pos)/pos');
    if (status === 'signed-out') router.replace('/(auth)/login');
  }, [status]);

  return (
    <View style={{ flex: 1, alignItems: 'center', justifyContent: 'center' }}>
      <ActivityIndicator size="large" />
    </View>
  );
}

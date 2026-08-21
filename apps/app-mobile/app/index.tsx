import { Text, View } from 'react-native';
import { spacing } from '@pharmacy/design-tokens';
import type { Currency } from '@pharmacy/types';

const currency: Currency = 'BDT';

export default function HomeScreen() {
  return (
    <View>
      <Text>Pharmacy Platform</Text>
      <Text>Mobile POS shell. Currency: {currency}. Spacing: {spacing.md}.</Text>
    </View>
  );
}

import { colors, tokens } from '@pharmacy/design-tokens';
import type { Currency } from '@pharmacy/types';

const currency: Currency = 'BDT';

export function App() {
  return (
    <main style={{ background: colors.background, color: colors.foreground, padding: tokens.spacing['2xl'] }}>
      <h1>Pharmacy Platform</h1>
      <p>Desktop POS shell with typed Tauri and hardware boundaries. Currency: {currency}.</p>
    </main>
  );
}

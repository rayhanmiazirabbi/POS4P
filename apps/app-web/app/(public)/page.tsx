import { colors, tokens } from '@pharmacy/design-tokens';
import type { Currency } from '@pharmacy/types';

const currency: Currency = 'BDT';

export default function PublicPage() {
  return (
    <main style={{ background: colors.background, color: colors.foreground, padding: tokens.spacing['2xl'] }}>
      <h1>Pharmacy Platform</h1>
      <p>Public pharmacy and catalogue routes start here. Currency: {currency}.</p>
    </main>
  );
}

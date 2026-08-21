export const colors = {
  background: '#F8FAFC',
  surface: '#FFFFFF',
  foreground: '#172033',
  muted: '#64748B',
  primary: '#0F766E',
  primaryForeground: '#FFFFFF',
  success: '#166534',
  warning: '#A16207',
  danger: '#B91C1C',
  border: '#CBD5E1',
} as const;

export const typography = {
  family: 'Inter, ui-sans-serif, system-ui, sans-serif',
  sizes: { sm: '0.875rem', md: '1rem', lg: '1.125rem', xl: '1.5rem', display: '2rem' },
  weights: { regular: 400, medium: 500, semibold: 600, bold: 700 },
} as const;

export const spacing = { xs: 4, sm: 8, md: 12, lg: 16, xl: 24, '2xl': 32 } as const;
export const breakpoints = { mobile: 0, tablet: 768, desktop: 1024 } as const;
export const density = { touchTarget: 44, compactRow: 36, comfortableRow: 48 } as const;

export const statusTokens = {
  active: { color: colors.success, label: 'Active' },
  pending: { color: colors.warning, label: 'Pending' },
  inactive: { color: colors.muted, label: 'Inactive' },
  error: { color: colors.danger, label: 'Error' },
} as const;

export type DesignTokens = {
  colors: typeof colors;
  typography: typeof typography;
  spacing: typeof spacing;
  breakpoints: typeof breakpoints;
  density: typeof density;
  statusTokens: typeof statusTokens;
};

export const tokens: DesignTokens = { colors, typography, spacing, breakpoints, density, statusTokens };

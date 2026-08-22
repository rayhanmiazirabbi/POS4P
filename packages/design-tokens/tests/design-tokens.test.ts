import { describe, expect, it } from 'vitest';
import { colors, statusTokens, tokens } from '../src/index';

/** WCAG 2.1 relative luminance (https://www.w3.org/TR/WCAG21/#dfn-relative-luminance). */
function luminance(hex: string): number {
  const channels = [0, 2, 4].map((offset) => parseInt(hex.slice(1 + offset, 3 + offset), 16) / 255);
  const linear = channels.map((channel) =>
    channel <= 0.04045 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4,
  );
  return 0.2126 * linear[0]! + 0.7152 * linear[1]! + 0.0722 * linear[2]!;
}

function contrast(foreground: string, background: string): number {
  const [light = 0, dark = 0] = [luminance(foreground), luminance(background)].sort((a, b) => b - a);
  return (light + 0.05) / (dark + 0.05);
}

describe('WCAG AA contrast', () => {
  // Every text-on-surface pair the palette defines. `border` is decorative and
  // excluded; `primaryForeground` pairs with `primary` as its text colour.
  const pairs: ReadonlyArray<[name: string, foreground: string, background: string]> = [
    ['foreground/background', colors.foreground, colors.background],
    ['foreground/surface', colors.foreground, colors.surface],
    ['muted/background', colors.muted, colors.background],
    ['muted/surface', colors.muted, colors.surface],
    ['primary/background', colors.primary, colors.background],
    ['primary/surface', colors.primary, colors.surface],
    ['primaryForeground/primary', colors.primaryForeground, colors.primary],
    ['success/surface', colors.success, colors.surface],
    ['warning/surface', colors.warning, colors.surface],
    ['danger/surface', colors.danger, colors.surface],
  ];

  it('meets AA (4.5:1) for every text pairing', () => {
    for (const [name, foreground, background] of pairs) {
      expect(contrast(foreground, background), name).toBeGreaterThanOrEqual(4.5);
    }
  });

  it('keeps every status colour readable on both surfaces', () => {
    for (const [name, token] of Object.entries(statusTokens)) {
      expect(contrast(token.color, colors.background), `${name}/background`).toBeGreaterThanOrEqual(4.5);
      expect(contrast(token.color, colors.surface), `${name}/surface`).toBeGreaterThanOrEqual(4.5);
    }
  });
});

describe('non-colour status cues', () => {
  it('conveys every status through a label as well as colour', () => {
    for (const [name, token] of Object.entries(statusTokens)) {
      expect(token.label.trim().length, name).toBeGreaterThan(0);
    }
    expect(Object.keys(statusTokens).length).toBe(4);
  });

  it('exposes the complete token set', () => {
    expect(Object.keys(tokens)).toEqual(['colors', 'typography', 'spacing', 'breakpoints', 'density', 'statusTokens']);
    expect(colors).toHaveProperty('danger');
  });
});

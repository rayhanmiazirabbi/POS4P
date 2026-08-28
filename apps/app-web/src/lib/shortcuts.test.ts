import { describe, expect, it } from 'vitest';

import { routeForShortcut } from './shortcuts';

describe('application shortcuts', () => {
  it('maps the fixed Alt number route order', () => {
    expect(routeForShortcut('1', true)).toBe('/pos');
    expect(routeForShortcut('6', true)).toBe('/settings');
  });

  it('ignores ordinary number input and unknown shortcuts', () => {
    expect(routeForShortcut('1', false)).toBeNull();
    expect(routeForShortcut('7', true)).toBeNull();
  });
});

import { describe, expect, it } from 'vitest';

import { buildGroupedShelfView, toShelfProduct, type ShelfSource } from '@pharmacy/sync';

/** The eight-row cap this till has always shown, exercised through the view. */
const TILL_CAP = 8;

function shelf(source: ShelfSource): ReturnType<typeof toShelfProduct> {
  return toShelfProduct(source);
}

describe('grouped shelf view (desktop till lens)', () => {
  const cache = [
    shelf({ id: 'p-1', sku: 'NAPA-500', name: 'Napa', salePrice: '12.00', manufacturerId: 'm-1', manufacturer: 'Beximco', dosageFormId: 'f-1', dosageForm: 'Tablet', genericName: 'Paracetamol' }),
    shelf({ id: 'p-2', sku: 'NAPA-SYR', name: 'Napa Syrup', salePrice: '30.00', manufacturerId: 'm-1', manufacturer: 'Beximco', dosageFormId: 'f-2', dosageForm: 'Syrup' }),
    shelf({ id: 'p-3', sku: 'ACE-500', name: 'Ace', salePrice: '10.00', manufacturerId: 'm-2', manufacturer: 'Square', dosageFormId: 'f-1', dosageForm: 'Tablet', genericName: 'Paracetamol' }),
  ];

  it('keeps the plain shelf list for a blank query', () => {
    expect(buildGroupedShelfView(cache, '', TILL_CAP)).toBeNull();
  });

  it('orders manufacturers by their best contained match, dosage groups inside', () => {
    const view = buildGroupedShelfView(cache, 'paracetamol', TILL_CAP);
    expect(view!.groups.map((group) => group.label)).toEqual(['Beximco', 'Square']);
    expect(view!.flatRows.map((entry) => entry.item.sku)).toEqual(['NAPA-500', 'ACE-500']);
  });

  it('caps the rows this till shows, before grouping', () => {
    const view = buildGroupedShelfView(cache, 'napa', 1);
    expect(view!.flatRows).toHaveLength(1);
    expect(view!.groups[0]!.count).toBe(1);
  });

  it('carries the match metadata a labelled row needs', () => {
    const view = buildGroupedShelfView([shelf({ id: 'p-n', sku: 'X-1', name: 'Napa', salePrice: '1.00' })], 'npa', TILL_CAP);
    const entry = view!.flatRows[0]!;
    expect(entry.matchQuality).toBe('fuzzy');
    expect(entry.matchedField).toBe('name');
  });
});

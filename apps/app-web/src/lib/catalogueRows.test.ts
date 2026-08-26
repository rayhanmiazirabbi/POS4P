import { describe, expect, it } from 'vitest';

import type { CatalogSearchItem } from '@pharmacy/api';
import { groupMedicineMatches, highlightMedicineSpans } from '@pharmacy/sync';

import { CATALOGUE_PAGE_SIZE, hasMoreResults, nextResultWindow, toRanked } from './catalogueRows';

/** A `GET /products/search` row, trimmed to what these tests exercise. */
function row(partials: Partial<CatalogSearchItem>): CatalogSearchItem {
  return {
    kind: 'catalog',
    catalogProductId: null,
    pharmacyProductId: null,
    name: '',
    shopStatus: 'absent',
    prescriptionRequired: false,
    matchedField: 'name',
    matchQuality: 'exact',
    matchedText: '',
    matchScore: 1,
    ...partials,
  } as CatalogSearchItem;
}

describe('toRanked + groupMedicineMatches', () => {
  it('groups server rows manufacturer-first, dosage-form-second, best match first', () => {
    const groups = groupMedicineMatches([
      toRanked(row({ name: 'Ace', manufacturerId: 'm-2', manufacturer: 'Square', dosageForm: 'Tablet', matchedField: 'genericName', matchQuality: 'exact' })),
      toRanked(row({ name: 'Napa', manufacturerId: 'm-1', manufacturer: 'Beximco', dosageForm: 'Tablet', matchedField: 'name', matchQuality: 'exact' })),
      toRanked(row({ name: 'Napa Syrup', manufacturerId: 'm-1', manufacturer: 'Beximco', dosageForm: 'Syrup', matchedField: 'name', matchQuality: 'partial' })),
    ]);
    expect(groups.map((group) => group.label)).toEqual(['Beximco', 'Square']);
    expect(groups[0]!.count).toBe(2);
    expect(groups[0]!.dosageGroups.map((group) => group.label)).toEqual(['Tablet', 'Syrup']);
  });

  it('keeps the server tier order when ranking rows for the groups', () => {
    const groups = groupMedicineMatches([
      toRanked(row({ name: 'Fuzzy Row', matchedField: 'name', matchQuality: 'fuzzy', matchScore: 0.75 })),
      toRanked(row({ name: 'Exact Row', matchedField: 'barcode', matchQuality: 'exact', matchScore: 1 })),
    ]);
    // The unattributed rows share the fallback manufacturer; the exact barcode
    // row leads within it.
    expect(groups[0]!.dosageGroups[0]!.items.map((entry) => entry.item.name)).toEqual(['Exact Row', 'Fuzzy Row']);
  });

  it('highlights the literal query tokens inside a catalogue name', () => {
    expect(highlightMedicineSpans('Napa Extra', 'napa')).toEqual([
      { text: 'Napa', hit: true },
      { text: ' Extra', hit: false },
    ]);
  });
});

describe('result window', () => {
  it('pages 50 at a time and stops at the total', () => {
    expect(CATALOGUE_PAGE_SIZE).toBe(50);
    expect(nextResultWindow(50, 180)).toBe(100);
    expect(nextResultWindow(150, 180)).toBe(180);
    // Nothing left: the window holds rather than growing past the total.
    expect(nextResultWindow(180, 180)).toBe(180);
  });

  it('reports whether anything remains to be appended', () => {
    expect(hasMoreResults(50, 180)).toBe(true);
    expect(hasMoreResults(180, 180)).toBe(false);
  });
});

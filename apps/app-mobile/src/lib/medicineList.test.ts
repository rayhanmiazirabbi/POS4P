import { describe, expect, it } from 'vitest';

import { medicineMatchesAreFuzzy } from '@pharmacy/sync';

import { buildMedicineListEntries } from './medicineList';
import type { ShelfSource } from '@pharmacy/sync';
import { toShelfProduct } from '@pharmacy/sync';

function shelf(row: ShelfSource): ReturnType<typeof toShelfProduct> {
  return toShelfProduct(row);
}

const counter = [
  shelf({ id: 'p-1', sku: 'NAPA-500', name: 'Napa', salePrice: '12.00', genericName: 'Paracetamol', strength: '500 mg', manufacturerId: 'm-1', manufacturer: 'Beximco', dosageFormId: 'f-1', dosageForm: 'Tablet' }),
  shelf({ id: 'p-2', sku: 'NAPA-SYR', name: 'Napa Syrup', salePrice: '30.00', manufacturerId: 'm-1', manufacturer: 'Beximco', dosageFormId: 'f-2', dosageForm: 'Syrup' }),
  shelf({ id: 'p-3', sku: 'ACE-500', name: 'Ace', salePrice: '10.00', genericName: 'Paracetamol', manufacturerId: 'm-2', manufacturer: 'Square', dosageFormId: 'f-1', dosageForm: 'Tablet' }),
];

describe('buildMedicineListEntries', () => {
  it('keeps the plain shelf list when no query is active', () => {
    const { entries, matches } = buildMedicineListEntries(counter, '   ');
    expect(matches).toEqual([]);
    expect(entries.map((entry) => entry.kind)).toEqual(['row', 'row', 'row']);
    const first = entries[0];
    if (first?.kind !== 'row') throw new Error('expected a plain row');
    expect(first.product.sku).toBe('NAPA-500');
  });

  it('interleaves manufacturer and dosage headings with the ranked rows', () => {
    const { entries } = buildMedicineListEntries(counter, 'paracetamol');
    expect(entries.map((entry) => entry.kind)).toEqual([
      'manufacturer', 'dosage', 'row', 'manufacturer', 'dosage', 'row',
    ]);
    const first = entries[0]!;
    expect(first.kind === 'manufacturer' && first.label).toBe('Beximco');
    expect(first.kind === 'manufacturer' && first.count).toBe(1);
    const second = entries[1]!;
    expect(second.kind === 'dosage' && second.label).toBe('Tablet');
    // Beximco leads: its exact generic match outranks Square's equal one only
    // by label order, and the rows carry the metadata the labels render from.
    const row = entries[2]!;
    expect(row.kind === 'row' && row.row?.matchQuality).toBe('exact');
  });

  it('falls back to the unclassified groups for rows without metadata', () => {
    const legacy = [shelf({ id: 'p-9', sku: 'MED-1', name: 'Paracetamol', salePrice: '5.00' })];
    const { entries } = buildMedicineListEntries(legacy, 'paracetamol');
    expect(entries[0]!.kind === 'manufacturer' && entries[0]!.label).toBe('Custom / manufacturer not set');
    expect(entries[1]!.kind === 'dosage' && entries[1]!.label).toBe('Dosage form not set');
  });

  it('exposes the match list so the screen can raise the fuzzy banner', () => {
    const only = [shelf({ id: 'p-n', sku: 'X-1', name: 'Napa', salePrice: '12.00' })];
    const { matches } = buildMedicineListEntries(only, 'npa');
    expect(medicineMatchesAreFuzzy(matches)).toBe(true);
  });
});

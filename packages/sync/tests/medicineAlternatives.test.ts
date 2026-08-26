import { describe, expect, it } from 'vitest';

import {
  findMedicineAlternatives,
  mergeMedicineAlternatives,
  type CatalogAlternativeLike,
  type MedicineAlternativeSource,
} from '../src/index';

/**
 * The same shelf the search tests use, extended with ids: Napa and Ace are the
 * same generic from different houses, and the rest exist to be correctly
 * excluded. The tier order asserted here is the contract the POS panels render.
 */
function row(overrides: Partial<MedicineAlternativeSource> & { id: string; name: string }): MedicineAlternativeSource {
  return {
    genericName: 'Paracetamol',
    strength: '500 mg',
    dosageForm: 'Tablet',
    manufacturer: 'Beximco',
    ...overrides,
  };
}

const TARGET = row({ id: 'sp-1', name: 'Napa 500', manufacturer: 'Beximco', manufacturerId: 'm-1' });

describe('findMedicineAlternatives', () => {
  it('ranks same strength + form first, then same strength, then the rest', () => {
    const shelf = [
      row({ id: 'sp-2', name: 'Ace 650', strength: '650 mg' }), // tier 2
      row({ id: 'sp-3', name: 'Ace 500 Syrup', dosageForm: 'Syrup' }), // tier 1
      row({ id: 'sp-4', name: 'Ace 500' }), // tier 0
    ];
    const tiers = findMedicineAlternatives(shelf, TARGET).map((alt) => alt.tier);
    expect(tiers).toEqual([0, 1, 2]);
  });

  it('excludes the target by id, not by name', () => {
    const sameNameOtherId = row({ id: 'sp-9', name: TARGET.name, manufacturer: 'Square' });
    const alternatives = findMedicineAlternatives([TARGET, sameNameOtherId], TARGET);
    expect(alternatives.map((alt) => alt.item.id)).toEqual(['sp-9']);
  });

  it('answers nothing for a row with no generic (custom products)', () => {
    const custom = { ...TARGET, id: 'sp-10', genericName: null };
    expect(findMedicineAlternatives([row({ id: 'sp-11', name: 'Ace 500' })], custom)).toEqual([]);
  });

  it('skips rows whose generic is empty or different', () => {
    const shelf = [
      row({ id: 'sp-12', name: 'Brufen 400', genericName: 'Ibuprofen' }),
      row({ id: 'sp-13', name: 'Unlabelled 500', genericName: null }),
      row({ id: 'sp-14', name: 'Ace 500' }),
    ];
    expect(findMedicineAlternatives(shelf, TARGET).map((alt) => alt.item.id)).toEqual(['sp-14']);
  });

  it('matches the generic after normalization, not as raw strings', () => {
    const shelf = [row({ id: 'sp-15', name: 'Ace 500', genericName: ' paracetamol ' })];
    const alternatives = findMedicineAlternatives(shelf, row({ ...TARGET, genericName: 'PARACETAMOL' }));
    expect(alternatives).toHaveLength(1);
    expect(alternatives[0]!.sameStrength).toBe(true);
  });

  it('treats a combination generic as its own medicine, not a superset', () => {
    const shelf = [row({ id: 'sp-16', name: 'Napa Extra', genericName: 'Paracetamol + Caffeine' })];
    expect(findMedicineAlternatives(shelf, TARGET)).toEqual([]);
  });

  it('ranks the target manufacturer last within a tier', () => {
    const shelf = [
      row({ id: 'sp-17', name: 'Zyncet 500', manufacturer: 'Beximco', manufacturerId: 'm-1' }),
      row({ id: 'sp-18', name: 'Ace 500', manufacturer: 'Square', manufacturerId: 'm-2' }),
    ];
    expect(findMedicineAlternatives(shelf, TARGET).map((alt) => alt.item.id)).toEqual(['sp-18', 'sp-17']);
  });

  it('falls back to the manufacturer label when ids are missing', () => {
    const shelf = [
      row({ id: 'sp-19', name: 'Zyncet 500', manufacturer: 'beximco', manufacturerId: null }),
      row({ id: 'sp-20', name: 'Ace 500', manufacturer: 'Square', manufacturerId: null }),
    ];
    expect(findMedicineAlternatives(shelf, TARGET).map((alt) => alt.item.id)).toEqual(['sp-20', 'sp-19']);
  });

  it('breaks ties deterministically by name then id', () => {
    const shelf = [
      row({ id: 'sp-b', name: 'Ace 500', manufacturer: 'Square' }),
      row({ id: 'sp-a', name: 'Ace 500', manufacturer: 'Square' }),
      row({ id: 'sp-c', name: 'Aaa 500', manufacturer: 'Square' }),
    ];
    expect(findMedicineAlternatives(shelf, TARGET).map((alt) => alt.item.id)).toEqual(['sp-c', 'sp-a', 'sp-b']);
  });

  it('treats a row with no strength as not the same strength', () => {
    const alternatives = findMedicineAlternatives([row({ id: 'sp-21', name: 'Ace', strength: null })], TARGET);
    expect(alternatives[0]!.sameStrength).toBe(false);
    expect(alternatives[0]!.tier).toBe(2);
  });

  it('compares the dosage form by id when the label is absent on the target', () => {
    const target = row({ id: 'sp-22', name: 'Napa 500', dosageForm: null, dosageFormId: 'df-1' });
    const shelf = [
      row({ id: 'sp-23', name: 'Ace 500', dosageForm: null, dosageFormId: 'df-1' }),
      row({ id: 'sp-24', name: 'Bez 500', dosageForm: null, dosageFormId: 'df-2' }),
    ];
    const alternatives = findMedicineAlternatives(shelf, target);
    expect(alternatives.map((alt) => alt.sameDosageForm)).toEqual([true, false]);
  });
});

describe('mergeMedicineAlternatives', () => {
  it('drops catalogue rows already sellable on this shelf', () => {
    const catalog = [
      { storeProductId: 'sp-4' }, // listed as a shelf alternative
      { storeProductId: 'sp-1' }, // is the row the cashier asked about
      { storeProductId: null }, // a brand this branch does not stock
    ];
    const merged = mergeMedicineAlternatives([{ id: 'sp-1' }, { id: 'sp-4' }], catalog);
    expect(merged).toEqual([{ storeProductId: null }]);
  });

  it('keeps every row when the shelf section is empty', () => {
    const catalog: CatalogAlternativeLike[] = [{ storeProductId: null }, {}, { storeProductId: 'sp-2' }];
    expect(mergeMedicineAlternatives([], catalog)).toEqual(catalog);
  });
});

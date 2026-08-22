import { describe, expect, it } from 'vitest';
import { conversionFactor, displayPackageQuantity, findByBarcode, findByIngredient, hasIngredient, parsePackageQuantity, parseStrength, searchMedicine, toBaseUnits, type MedicineProduct } from '../src/index';

const product: MedicineProduct = { id: 'p1', name: 'Paracetamol 500', aliases: ['Napa 500'], barcodes: ['123 456'], ingredients: [{ name: 'Paracetamol', strength: '500 mg' }], baseUnit: 'tablet', conversions: [{ from: 'strip', to: 'tablet', factor: 10 }] };
const combination: MedicineProduct = { id: 'p2', name: 'Napa Extra', aliases: [], barcodes: [], ingredients: [{ name: 'Paracetamol', strength: '500 mg' }, { name: 'Caffeine', strength: '65 mg' }], baseUnit: 'tablet', conversions: [{ from: 'box', to: 'strip', factor: 20 }] };

describe('medicine primitives', () => {
  it('matches aliases and barcodes without claiming clinical equivalence', () => {
    expect(searchMedicine([product], 'napa 500')[0]?.matchedBy).toBe('alias');
    expect(findByBarcode([product], '123456')).toHaveLength(1);
  });
  it('converts base units and preserves packaging display', () => {
    expect(toBaseUnits(2, 'strip', product)).toBe(20);
    expect(displayPackageQuantity(2, 'strip', 'tablet', 10)).toBe('2 strip (20 tablet)');
  });
});

describe('strength parsing', () => {
  it('parses common and unitless strengths', () => {
    expect(parseStrength('500 mg')).toEqual({ value: 500, unit: 'mg' });
    expect(parseStrength('2.5ml')).toEqual({ value: 2.5, unit: 'ml' });
    expect(parseStrength('500')).toEqual({ value: 500, unit: 'unit' });
  });
  it('rejects malformed strength text', () => {
    expect(parseStrength('abc')).toBeNull();
    expect(parseStrength('-5 mg')).toBeNull();
    expect(parseStrength('')).toBeNull();
  });
  it('matches combination products by ingredient strength', () => {
    const matches = searchMedicine([product, combination], 'paracetamol 500mg');
    expect(matches.map((match) => match.product.id)).toContain('p2');
    expect(hasIngredient(combination, 'caffeine')).toBe(true);
    expect(findByIngredient([product, combination], ' Caffeine ').map((item) => item.id)).toEqual(['p2']);
  });
});

describe('transitive conversions', () => {
  const conversions = [{ from: 'box' as const, to: 'strip' as const, factor: 20 }, { from: 'strip' as const, to: 'tablet' as const, factor: 10 }];
  it('resolves multi-hop factors through the graph', () => {
    expect(conversionFactor('box', 'tablet', conversions)).toBe(200);
    expect(conversionFactor('tablet', 'tablet', conversions)).toBe(1);
  });
  it('fails explicitly when no path exists', () => {
    expect(() => conversionFactor('bottle', 'tablet', conversions)).toThrow('No conversion');
  });
});

describe('package display round trip', () => {
  it('parses back displayed package quantities', () => {
    const display = displayPackageQuantity(1.5, 'box', 'tablet', 200);
    expect(parsePackageQuantity(display)).toEqual({ quantity: 1.5, packageUnit: 'box', baseQuantity: 300, baseUnit: 'tablet' });
  });
  it('returns null for malformed displays', () => {
    expect(parsePackageQuantity('not a display')).toBeNull();
  });
});

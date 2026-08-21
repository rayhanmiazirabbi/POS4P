import { describe, expect, it } from 'vitest';
import { displayPackageQuantity, findByBarcode, searchMedicine, toBaseUnits, type MedicineProduct } from '../src/index';

const product: MedicineProduct = { id: 'p1', name: 'Paracetamol 500', aliases: ['Napa 500'], barcodes: ['123 456'], ingredients: [{ name: 'Paracetamol', strength: '500 mg' }], baseUnit: 'tablet', conversions: [{ from: 'strip', to: 'tablet', factor: 10 }] };

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

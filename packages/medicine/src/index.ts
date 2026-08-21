import { normalizeBarcode } from '@pharmacy/core';

export type MedicineUnit = 'tablet' | 'capsule' | 'strip' | 'box' | 'bottle' | 'ml' | 'g' | 'kg' | 'piece' | 'pack';
export type Ingredient = { name: string; strength?: string };
export type UnitConversion = { from: MedicineUnit; to: MedicineUnit; factor: number };
export type MedicineProduct = {
  id: string;
  name: string;
  aliases: readonly string[];
  barcodes: readonly string[];
  ingredients: readonly Ingredient[];
  baseUnit: MedicineUnit;
  conversions: readonly UnitConversion[];
};
export type MedicineMatch = { product: MedicineProduct; confidence: number; matchedBy: 'name' | 'alias' | 'barcode' };

function normalized(value: string): string { return value.trim().toLocaleLowerCase().replace(/\s+/g, ' '); }

export function findByBarcode(products: readonly MedicineProduct[], barcode: string): MedicineProduct[] {
  const wanted = normalizeBarcode(barcode);
  return products.filter((product) => product.barcodes.some((value) => normalizeBarcode(value) === wanted));
}

export function searchMedicine(products: readonly MedicineProduct[], query: string): MedicineMatch[] {
  const wanted = normalized(query);
  const barcodeMatches = findByBarcode(products, query);
  return products.map((product) => {
    const name = normalized(product.name);
    const alias = product.aliases.some((value) => normalized(value) === wanted);
    const nameMatch = name === wanted;
    const contains = name.includes(wanted) || product.aliases.some((value) => normalized(value).includes(wanted));
    const barcode = barcodeMatches.some((match) => match.id === product.id);
    if (barcode) return { product, confidence: 1, matchedBy: 'barcode' as const };
    if (nameMatch) return { product, confidence: 1, matchedBy: 'name' as const };
    if (alias) return { product, confidence: 0.95, matchedBy: 'alias' as const };
    if (contains) return { product, confidence: 0.7, matchedBy: name.includes(wanted) ? 'name' as const : 'alias' as const };
    return null;
  }).filter((match): match is MedicineMatch => match !== null).sort((a, b) => b.confidence - a.confidence || a.product.name.localeCompare(b.product.name));
}

export function convertQuantity(quantity: number, from: MedicineUnit, to: MedicineUnit, conversions: readonly UnitConversion[]): number {
  if (!Number.isFinite(quantity) || quantity < 0) throw new Error('Quantity must be non-negative');
  if (from === to) return quantity;
  const conversion = conversions.find((item) => item.from === from && item.to === to);
  if (!conversion || !Number.isFinite(conversion.factor) || conversion.factor <= 0) throw new Error(`No conversion from ${from} to ${to}`);
  return quantity * conversion.factor;
}

export function toBaseUnits(quantity: number, unit: MedicineUnit, product: Pick<MedicineProduct, 'baseUnit' | 'conversions'>): number {
  return convertQuantity(quantity, unit, product.baseUnit, product.conversions);
}

export function displayPackageQuantity(quantity: number, packageUnit: MedicineUnit, baseUnit: MedicineUnit, unitsPerPackage: number): string {
  if (!Number.isFinite(quantity) || quantity < 0 || !Number.isFinite(unitsPerPackage) || unitsPerPackage <= 0) throw new Error('Invalid package quantity');
  const baseQuantity = quantity * unitsPerPackage;
  return `${quantity} ${packageUnit} (${baseQuantity} ${baseUnit})`;
}

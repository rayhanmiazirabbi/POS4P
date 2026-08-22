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
export type ParsedStrength = { value: number; unit: string };
export type PackageQuantity = { quantity: number; packageUnit: string; baseQuantity: number; baseUnit: string };

const STRENGTH_PATTERN = /^(\d+(?:\.\d+)?)\s*(mg|mcg|g|ml|iu|%)?$/i;
const PACKAGE_PATTERN = /^(\d+(?:\.\d+)?) ([a-z]+) \((\d+(?:\.\d+)?) ([a-z]+)\)$/;
function normalized(value: string): string { return value.trim().toLocaleLowerCase().replace(/\s+/g, ' '); }

export function parseStrength(text: string): ParsedStrength | null {
  const match = STRENGTH_PATTERN.exec(text.trim());
  if (!match || !match[1]) return null;
  const value = Number(match[1]);
  if (!Number.isFinite(value) || value <= 0) return null;
  return { value, unit: (match[2] ?? '').toLowerCase() || 'unit' };
}

export function findByBarcode(products: readonly MedicineProduct[], barcode: string): MedicineProduct[] {
  const wanted = normalizeBarcode(barcode);
  return products.filter((product) => product.barcodes.some((value) => normalizeBarcode(value) === wanted));
}

export function findIngredient(product: Pick<MedicineProduct, 'ingredients'>, name: string): Ingredient | null {
  const wanted = normalized(name);
  return product.ingredients.find((ingredient) => normalized(ingredient.name) === wanted) ?? null;
}

export function hasIngredient(product: Pick<MedicineProduct, 'ingredients'>, name: string): boolean {
  return findIngredient(product, name) !== null;
}

export function findByIngredient(products: readonly MedicineProduct[], name: string): MedicineProduct[] {
  return products.filter((product) => hasIngredient(product, name));
}

export function searchMedicine(products: readonly MedicineProduct[], query: string): MedicineMatch[] {
  const trimmed = query.trim();
  const wanted = normalized(trimmed);
  const barcodeMatches = findByBarcode(products, query);
  const strengthTail = /\s*\d+(?:\.\d+)?\s*(?:mg|mcg|g|ml|iu|%)?\s*$/i.exec(trimmed);
  const strength = strengthTail ? parseStrength(strengthTail[0] ?? '') : null;
  const ingredientName = strength && strengthTail ? wanted.slice(0, strengthTail.index).trim() : '';
  return products.map((product) => {
    const name = normalized(product.name);
    const alias = product.aliases.some((value) => normalized(value) === wanted);
    const nameMatch = name === wanted;
    const contains = name.includes(wanted) || product.aliases.some((value) => normalized(value).includes(wanted));
    const ingredientMatch = strength !== null && ingredientName.length > 0 && product.ingredients.some((ingredient) => {
      const parsed = ingredient.strength ? parseStrength(ingredient.strength) : null;
      return parsed !== null && parsed.value === strength.value && parsed.unit === strength.unit && (normalized(ingredient.name).includes(ingredientName) || ingredientName.includes(normalized(ingredient.name)));
    });
    const barcode = barcodeMatches.some((match) => match.id === product.id);
    if (barcode) return { product, confidence: 1, matchedBy: 'barcode' as const };
    if (nameMatch) return { product, confidence: 1, matchedBy: 'name' as const };
    if (alias) return { product, confidence: 0.95, matchedBy: 'alias' as const };
    if (contains) return { product, confidence: 0.7, matchedBy: name.includes(wanted) ? 'name' as const : 'alias' as const };
    if (ingredientMatch) return { product, confidence: 0.6, matchedBy: 'name' as const };
    return null;
  }).filter((match): match is MedicineMatch => match !== null).sort((a, b) => b.confidence - a.confidence || a.product.name.localeCompare(b.product.name));
}

export function conversionFactor(from: MedicineUnit, to: MedicineUnit, conversions: readonly UnitConversion[]): number {
  if (from === to) return 1;
  const edges = new Map<MedicineUnit, Array<{ to: MedicineUnit; factor: number }>>();
  for (const item of conversions) {
    if (!Number.isFinite(item.factor) || item.factor <= 0) throw new Error(`Invalid conversion factor for ${item.from} to ${item.to}`);
    const outgoing = edges.get(item.from) ?? [];
    outgoing.push({ to: item.to, factor: item.factor });
    edges.set(item.from, outgoing);
  }
  const visited = new Set<MedicineUnit>([from]);
  const queue: Array<{ unit: MedicineUnit; factor: number }> = [{ unit: from, factor: 1 }];
  while (queue.length > 0) {
    const current = queue.shift();
    if (!current) break;
    if (current.unit === to) return current.factor;
    for (const edge of edges.get(current.unit) ?? []) {
      if (visited.has(edge.to)) continue;
      visited.add(edge.to);
      queue.push({ unit: edge.to, factor: current.factor * edge.factor });
    }
  }
  throw new Error(`No conversion from ${from} to ${to}`);
}

export function convertQuantity(quantity: number, from: MedicineUnit, to: MedicineUnit, conversions: readonly UnitConversion[]): number {
  if (!Number.isFinite(quantity) || quantity < 0) throw new Error('Quantity must be non-negative');
  return quantity * conversionFactor(from, to, conversions);
}

export function toBaseUnits(quantity: number, unit: MedicineUnit, product: Pick<MedicineProduct, 'baseUnit' | 'conversions'>): number {
  return convertQuantity(quantity, unit, product.baseUnit, product.conversions);
}

export function displayPackageQuantity(quantity: number, packageUnit: MedicineUnit, baseUnit: MedicineUnit, unitsPerPackage: number): string {
  if (!Number.isFinite(quantity) || quantity < 0 || !Number.isFinite(unitsPerPackage) || unitsPerPackage <= 0) throw new Error('Invalid package quantity');
  const baseQuantity = quantity * unitsPerPackage;
  return `${quantity} ${packageUnit} (${baseQuantity} ${baseUnit})`;
}

export function parsePackageQuantity(display: string): PackageQuantity | null {
  const match = PACKAGE_PATTERN.exec(display.trim());
  if (!match || !match[1] || !match[3]) return null;
  const quantity = Number(match[1]);
  const baseQuantity = Number(match[3]);
  if (!Number.isFinite(quantity) || !Number.isFinite(baseQuantity)) return null;
  return { quantity, packageUnit: match[2] ?? '', baseQuantity, baseUnit: match[4] ?? '' };
}

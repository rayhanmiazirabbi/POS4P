import {
  groupShelfMatches,
  matchShelf,
  type RankedMedicine,
  type ShelfMatch,
  type ShelfProduct,
} from '@pharmacy/sync';

/** One render entry: a group heading or a medicine row. */
export type ListEntry =
  | { kind: 'manufacturer'; label: string; count: number }
  | { kind: 'dosage'; label: string; count: number }
  | { kind: 'row'; product: ShelfProduct; row: RankedMedicine<ShelfProduct> | null };

/**
 * The flat list the counter's FlatList renders: manufacturer and dosage
 * headings interleaved with the medicine rows, in the shared ranked order.
 * An empty query keeps the plain shelf list -- grouping is a search-time
 * presentation only.
 */
export function buildMedicineListEntries(
  products: readonly ShelfProduct[],
  query: string,
): { entries: readonly ListEntry[]; matches: readonly ShelfMatch[] } {
  if (query.trim() === '') {
    return {
      entries: products.map((product) => ({ kind: 'row' as const, row: null, product })),
      matches: [],
    };
  }
  const matches = matchShelf(products, query);
  const entries: ListEntry[] = [];
  for (const manufacturer of groupShelfMatches(matches)) {
    entries.push({ kind: 'manufacturer', label: manufacturer.label, count: manufacturer.count });
    for (const dosage of manufacturer.dosageGroups) {
      entries.push({ kind: 'dosage', label: dosage.label, count: dosage.items.length });
      for (const row of dosage.items) entries.push({ kind: 'row', row, product: row.item });
    }
  }
  return { entries, matches };
}

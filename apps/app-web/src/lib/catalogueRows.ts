import type { CatalogSearchItem } from '@pharmacy/api';
import { medicineMatchRank, type RankedMedicine } from '@pharmacy/sync';

/** A server search row as the shared grouper sees it: item + match metadata. */
export function toRanked(row: CatalogSearchItem): RankedMedicine<CatalogSearchItem> {
  return {
    item: row,
    matchedField: row.matchedField,
    matchQuality: row.matchQuality,
    matchedText: row.matchedText,
    matchScore: row.matchScore,
    rank: medicineMatchRank(row.matchedField, row.matchQuality),
  };
}

export const CATALOGUE_PAGE_SIZE = 50;

/**
 * The next "Show more results" window. The server keeps one deterministic
 * order, so widening the window appends later pages into the groups already on
 * screen instead of rebuilding them; it never shrinks below what is shown.
 */
export function nextResultWindow(current: number, total: number): number {
  return total > current ? Math.min(current + CATALOGUE_PAGE_SIZE, total) : current;
}

/** Whether the "Show more results" control still has anything to reveal. */
export function hasMoreResults(shown: number, total: number): boolean {
  return shown < total;
}

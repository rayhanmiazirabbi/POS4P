/** Every field the server may report; the local matcher returns a subset. */
export type MedicineMatchField = 'barcode' | 'sku' | 'name' | 'genericName' | 'alias' | 'strength' | 'dosageForm';
export type MedicineMatchQuality = 'exact' | 'partial' | 'fuzzy' | 'supporting';

export type MedicineSearchValue = {
  name: string;
  genericName?: string | null;
  strength?: string | null;
  dosageForm?: string | null;
  manufacturerId?: string | null;
  manufacturer?: string | null;
};

export type MedicineTextMatch = {
  matchedField: MedicineMatchField;
  matchQuality: MedicineMatchQuality;
  matchedText: string;
  matchScore: number;
  /** Lower is safer/more relevant. Identifier ranks 0 and 1 are owned by shelf.ts. */
  rank: number;
};

export type RankedMedicine<T extends MedicineSearchValue> = MedicineTextMatch & { item: T };

export type MedicineDosageGroup<T extends MedicineSearchValue> = {
  key: string;
  label: string;
  bestRank: number;
  bestScore: number;
  items: readonly RankedMedicine<T>[];
};

export type MedicineManufacturerGroup<T extends MedicineSearchValue> = {
  key: string;
  label: string;
  bestRank: number;
  bestScore: number;
  count: number;
  dosageGroups: readonly MedicineDosageGroup<T>[];
};

const UNIT_PATTERN = /\b\d+(?:\.\d+)?\s*(?:mcg|mg|gm|g|ml|l|iu|units?|meq|mmol|%)\b/giu;

/** Stable normalization shared by all offline counter shells. */
export function normalizeMedicineText(value: string): string {
  return value
    .normalize('NFKC')
    .toLocaleLowerCase()
    .replace(/[‐‑‒–—−]/g, '-')
    .replace(/[^\p{L}\p{N}_+/%.-]+/gu, ' ')
    .replace(/\s+/g, ' ')
    .trim()
    .replace(/(\d)\s+(mcg|mg|gm|g|ml|l|iu|unit|units|meq|mmol|%)(?=\b|\/)/giu, '$1$2');
}

function editBudget(length: number): number {
  if (length < 3) return 0;
  if (length <= 5) return 1;
  if (length <= 9) return 2;
  return 3;
}

/** Optimal-string-alignment Damerau-Levenshtein; adjacent transposition costs one. */
export function medicineEditDistance(leftRaw: string, rightRaw: string): number {
  const left = Array.from(normalizeMedicineText(leftRaw));
  const right = Array.from(normalizeMedicineText(rightRaw));
  const rows = Array.from({ length: left.length + 1 }, () => Array<number>(right.length + 1).fill(0));
  for (let i = 0; i <= left.length; i += 1) rows[i]![0] = i;
  for (let j = 0; j <= right.length; j += 1) rows[0]![j] = j;
  for (let i = 1; i <= left.length; i += 1) {
    for (let j = 1; j <= right.length; j += 1) {
      const substitution = left[i - 1] === right[j - 1] ? 0 : 1;
      let distance = Math.min(
        rows[i - 1]![j]! + 1,
        rows[i]![j - 1]! + 1,
        rows[i - 1]![j - 1]! + substitution,
      );
      if (
        i > 1 && j > 1 &&
        left[i - 1] === right[j - 2] &&
        left[i - 2] === right[j - 1]
      ) {
        distance = Math.min(distance, rows[i - 2]![j - 2]! + 1);
      }
      rows[i]![j] = distance;
    }
  }
  return rows[left.length]![right.length]!;
}

function fuzzyScore(query: string, field: string): number | null {
  if (query.length < 3) return null;
  const words = field.split(' ').filter(Boolean);
  const queryWords = query.split(' ').filter(Boolean);
  const width = Math.max(1, queryWords.length);
  const candidates = new Set<string>([field]);
  for (let index = 0; index < words.length; index += 1) {
    candidates.add(words.slice(index, index + width).join(' '));
  }
  let best: number | null = null;
  for (const candidate of candidates) {
    if (candidate === '') continue;
    const distance = medicineEditDistance(query, candidate);
    const score = 1 - distance / Math.max(Array.from(query).length, Array.from(candidate).length);
    if (distance <= editBudget(Array.from(query).length) && score >= 0.70) {
      best = best === null ? score : Math.max(best, score);
    }
  }
  return best;
}

function removePhrase(value: string, phrase: string): string {
  if (phrase === '') return value;
  return value
    .replace(new RegExp(`(^|\\s)${escapeRegex(phrase)}(?=\\s|$)`, 'gu'), ' ')
    .replace(/\s+/g, ' ')
    .trim();
}

function escapeRegex(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

function queryForCandidate(query: string, item: MedicineSearchValue): { text: string; supports: MedicineTextMatch[]; rejected: boolean } {
  let text = query;
  const supports: MedicineTextMatch[] = [];
  const dosage = normalizeMedicineText(item.dosageForm ?? '');
  if (dosage !== '' && (` ${text} `).includes(` ${dosage} `)) {
    text = removePhrase(text, dosage);
    supports.push({ matchedField: 'dosageForm', matchQuality: 'supporting', matchedText: item.dosageForm ?? '', matchScore: 1, rank: 10 });
  }

  const requestedStrengths = Array.from(query.matchAll(UNIT_PATTERN), (match) => normalizeMedicineText(match[0]));
  const strength = normalizeMedicineText(item.strength ?? '');
  if (requestedStrengths.length > 0) {
    if (strength === '' || requestedStrengths.some((term) => !strength.includes(term))) {
      return { text, supports, rejected: true };
    }
    for (const term of requestedStrengths) text = removePhrase(text, term);
    supports.push({ matchedField: 'strength', matchQuality: 'supporting', matchedText: item.strength ?? '', matchScore: 1, rank: 10 });
  }
  if (strength !== '') {
    // A bare "500" ("napa 500 tablet") is a strength the cashier left the unit off
    // of. It matches only the digits written on the row: 500 does not stand in for
    // 650, and a row with no strength at all keeps the number for its name.
    const strengthNumbers = new Set(Array.from(strength.matchAll(/\d+(?:\.\d+)?/g), (match) => match[0]));
    for (const token of text.split(' ')) {
      if (!/^\d+(?:\.\d+)?$/.test(token)) continue;
      if (!strengthNumbers.has(token)) return { text, supports, rejected: true };
      text = removePhrase(text, token);
      supports.push({ matchedField: 'strength', matchQuality: 'supporting', matchedText: item.strength ?? '', matchScore: 1, rank: 10 });
    }
  }
  return { text, supports, rejected: false };
}

/** Rank one medicine by brand/generic text after applying exact strength/form support terms. */
export function matchMedicineText(item: MedicineSearchValue, rawQuery: string): MedicineTextMatch | null {
  const query = normalizeMedicineText(rawQuery);
  if (query === '') return null;
  const name = normalizeMedicineText(item.name);
  const generic = normalizeMedicineText(item.genericName ?? '');

  // A custom product often embeds its strength in its local name, so full-field
  // checks happen before candidate support terms are removed.
  if (name === query) return { matchedField: 'name', matchQuality: 'exact', matchedText: item.name, matchScore: 1, rank: 2 };
  if (generic !== '' && generic === query) return { matchedField: 'genericName', matchQuality: 'exact', matchedText: item.genericName ?? '', matchScore: 1, rank: 3 };

  const parsed = queryForCandidate(query, item);
  if (parsed.rejected) return null;
  const text = parsed.text;
  if (text === '') return parsed.supports[0] ?? null;

  if (name === text) return { matchedField: 'name', matchQuality: 'exact', matchedText: item.name, matchScore: 1, rank: 2 };
  if (generic !== '' && generic === text) return { matchedField: 'genericName', matchQuality: 'exact', matchedText: item.genericName ?? '', matchScore: 1, rank: 3 };
  if (name.includes(text)) return { matchedField: 'name', matchQuality: 'partial', matchedText: item.name, matchScore: text.length / name.length, rank: 5 };
  if (generic !== '' && generic.includes(text)) return { matchedField: 'genericName', matchQuality: 'partial', matchedText: item.genericName ?? '', matchScore: text.length / generic.length, rank: 6 };

  const nameScore = fuzzyScore(text, name);
  const genericScore = generic === '' ? null : fuzzyScore(text, generic);
  if (nameScore !== null) return { matchedField: 'name', matchQuality: 'fuzzy', matchedText: item.name, matchScore: nameScore, rank: 8 };
  if (genericScore !== null) return { matchedField: 'genericName', matchQuality: 'fuzzy', matchedText: item.genericName ?? '', matchScore: genericScore, rank: 9 };
  return null;
}

function compareRanked<T extends MedicineSearchValue>(left: RankedMedicine<T>, right: RankedMedicine<T>): number {
  return left.rank - right.rank || right.matchScore - left.matchScore || left.item.name.localeCompare(right.item.name);
}

/** Manufacturer -> dosage hierarchy; each group inherits its best child's rank. */
export function groupMedicineMatches<T extends MedicineSearchValue>(matches: readonly RankedMedicine<T>[]): readonly MedicineManufacturerGroup<T>[] {
  const manufacturers = new Map<string, { label: string; matches: RankedMedicine<T>[] }>();
  for (const match of matches) {
    const manufacturer = match.item.manufacturer?.trim() || 'Custom / manufacturer not set';
    const key = match.item.manufacturerId ?? `label:${normalizeMedicineText(manufacturer)}`;
    const bucket = manufacturers.get(key) ?? { label: manufacturer, matches: [] };
    bucket.matches.push(match);
    manufacturers.set(key, bucket);
  }

  const groups: MedicineManufacturerGroup<T>[] = [];
  for (const [key, manufacturer] of manufacturers) {
    const dosages = new Map<string, { label: string; matches: RankedMedicine<T>[] }>();
    for (const match of manufacturer.matches) {
      const dosage = match.item.dosageForm?.trim() || 'Dosage form not set';
      const dosageKey = `label:${normalizeMedicineText(dosage)}`;
      const bucket = dosages.get(dosageKey) ?? { label: dosage, matches: [] };
      bucket.matches.push(match);
      dosages.set(dosageKey, bucket);
    }
    const dosageGroups = Array.from(dosages, ([dosageKey, dosage]) => {
      const items = dosage.matches.sort(compareRanked);
      return { key: dosageKey, label: dosage.label, bestRank: items[0]!.rank, bestScore: items[0]!.matchScore, items };
    }).sort((a, b) => a.bestRank - b.bestRank || b.bestScore - a.bestScore || a.label.localeCompare(b.label));
    groups.push({
      key,
      label: manufacturer.label,
      bestRank: dosageGroups[0]!.bestRank,
      bestScore: dosageGroups[0]!.bestScore,
      count: manufacturer.matches.length,
      dosageGroups,
    });
  }
  return groups.sort((a, b) => a.bestRank - b.bestRank || b.bestScore - a.bestScore || a.label.localeCompare(b.label));
}

/**
 * The rank a server search row carries, derived from the metadata
 * `GET /products/search` reports. The same tier order the Python ranker uses;
 * shared here so the catalogue screen groups API rows exactly as the POS shells
 * group local shelf matches.
 */
const MEDICINE_MATCH_RANKS: Record<string, number> = {
  'barcode:exact': 0,
  'sku:exact': 1,
  'name:exact': 2,
  'genericName:exact': 3,
  'alias:exact': 4,
  'name:partial': 5,
  'genericName:partial': 6,
  'alias:partial': 7,
  'name:fuzzy': 8,
  'genericName:fuzzy': 9,
  'strength:supporting': 10,
  'dosageForm:supporting': 10,
};

export function medicineMatchRank(field: MedicineMatchField, quality: MedicineMatchQuality): number {
  return MEDICINE_MATCH_RANKS[`${field}:${quality}`] ?? 10;
}

/** What a row says about why it is on the screen, in a cashier's words. */
export function describeMedicineMatch(match: { matchedField: MedicineMatchField; matchQuality: MedicineMatchQuality }): string {
  const key = `${match.matchedField}:${match.matchQuality}`;
  if (key === 'barcode:exact') return 'Exact barcode';
  if (key === 'sku:exact') return 'Exact SKU';
  if (key === 'name:exact') return 'Exact brand match';
  if (key === 'genericName:exact') return 'Exact generic match';
  if (key === 'alias:exact') return 'Exact alias match';
  if (key === 'name:partial') return 'Brand name match';
  if (key === 'genericName:partial') return 'Generic name match';
  if (key === 'alias:partial') return 'Alias match';
  if (key === 'name:fuzzy') return 'Closest brand match';
  if (key === 'genericName:fuzzy') return 'Closest generic match';
  if (key === 'strength:supporting') return 'Matched by strength';
  return 'Matched by dosage form';
}

/**
 * True when nothing on the list is better than a typo guess -- the screen owes
 * the cashier the "no exact match" banner rather than a list that reads as
 * confirmed results.
 */
export function medicineMatchesAreFuzzy(matches: readonly { matchQuality: MedicineMatchQuality }[]): boolean {
  return matches.length > 0 && matches.every((match) => match.matchQuality === 'fuzzy');
}

export type HighlightSpan = { text: string; hit: boolean };

/**
 * Split `text` around literal occurrences of the query's tokens, so a screen can
 * mark exactly what was typed. Matching is plain case-insensitive equality of
 * substrings -- no fuzzy hits are highlighted, because a mark under a word the
 * cashier did not type is a lie about why the row appeared.
 */
export function highlightMedicineSpans(text: string, query: string): readonly HighlightSpan[] {
  const tokens = Array.from(new Set(query.split(/\s+/).map((token) => token.trim()).filter((token) => token !== '')))
    .sort((left, right) => right.length - left.length);
  if (tokens.length === 0 || text === '') return [{ text, hit: false }];

  const haystack = text.toLocaleLowerCase();
  const ranges: Array<[number, number]> = [];
  for (const token of tokens) {
    const needle = token.toLocaleLowerCase();
    let from = haystack.indexOf(needle);
    while (from !== -1) {
      ranges.push([from, from + needle.length]);
      from = haystack.indexOf(needle, from + needle.length);
    }
  }
  if (ranges.length === 0) return [{ text, hit: false }];

  ranges.sort((left, right) => left[0] - right[0] || left[1] - right[1]);
  const merged: Array<[number, number]> = [ranges[0]!];
  for (const range of ranges.slice(1)) {
    const last = merged[merged.length - 1]!;
    if (range[0] <= last[1]) last[1] = Math.max(last[1], range[1]);
    else merged.push(range);
  }

  const spans: HighlightSpan[] = [];
  let cursor = 0;
  for (const [start, end] of merged) {
    if (cursor < start) spans.push({ text: text.slice(cursor, start), hit: false });
    spans.push({ text: text.slice(start, end), hit: true });
    cursor = end;
  }
  if (cursor < text.length) spans.push({ text: text.slice(cursor), hit: false });
  return spans;
}

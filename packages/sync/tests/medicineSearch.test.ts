import { describe, expect, it } from 'vitest';

import {
  describeMedicineMatch,
  groupMedicineMatches,
  highlightMedicineSpans,
  matchMedicineText,
  medicineEditDistance,
  medicineMatchRank,
  medicineMatchesAreFuzzy,
  normalizeMedicineText,
  type MedicineSearchValue,
  type MedicineTextMatch,
} from '../src/index';

/**
 * Golden vectors for the shared scorer. The same rows live in
 * `backend/tests/test_medicine_search.py`; when one changes here it changes
 * there, because the whole point of a shared contract is that a till in
 * Sylhet and a server in Dhaka rank "npa" the same way.
 */
const GOLDEN: ReadonlyArray<{
  label: string;
  query: string;
  item: MedicineSearchValue;
  expect: { matchedField: MedicineTextMatch['matchedField']; matchQuality: MedicineTextMatch['matchQuality']; matchScore?: number } | null;
}> = [
  { label: 'exact brand', query: 'Napa Extra', item: { name: 'Napa Extra', genericName: 'Paracetamol + Caffeine' }, expect: { matchedField: 'name', matchQuality: 'exact', matchScore: 1 } },
  { label: 'case and spacing fold onto the brand', query: '  NAPA   extra ', item: { name: 'Napa Extra' }, expect: { matchedField: 'name', matchQuality: 'exact', matchScore: 1 } },
  { label: 'exact generic', query: 'paracetamol', item: { name: 'Napa 500', genericName: 'Paracetamol' }, expect: { matchedField: 'genericName', matchQuality: 'exact', matchScore: 1 } },
  { label: 'partial brand', query: 'napa', item: { name: 'Napa Extra' }, expect: { matchedField: 'name', matchQuality: 'partial' } },
  { label: 'partial generic', query: 'paracet', item: { name: 'Napa 500', genericName: 'Paracetamol' }, expect: { matchedField: 'genericName', matchQuality: 'partial' } },
  { label: 'typo: omission (npa -> Napa)', query: 'npa', item: { name: 'Napa' }, expect: { matchedField: 'name', matchQuality: 'fuzzy', matchScore: 0.75 } },
  { label: 'typo: substitution (omeprazle -> Omeprazole)', query: 'omeprazle', item: { name: 'Omeprazole' }, expect: { matchedField: 'name', matchQuality: 'fuzzy' } },
  { label: 'typo: transposition (paracetmaol -> Paracetamol)', query: 'paracetmaol', item: { name: 'Paracetamol', genericName: 'Paracetamol' }, expect: { matchedField: 'name', matchQuality: 'fuzzy' } },
  { label: 'typo on the generic, not the brand', query: 'paracetamal', item: { name: 'Napa 500', genericName: 'Paracetamol' }, expect: { matchedField: 'genericName', matchQuality: 'fuzzy' } },
  { label: 'one and two character queries never fuzzy match', query: 'yx', item: { name: 'Napa' }, expect: null },
  { label: 'a short prefix is still a partial brand match', query: 'na', item: { name: 'Napa' }, expect: { matchedField: 'name', matchQuality: 'partial' } },
  { label: 'a weak look-alike is rejected', query: 'naproxen', item: { name: 'Napa' }, expect: null },
  { label: 'strength + dosage form support a brand term (napa 500 tablet)', query: 'napa 500 tablet', item: { name: 'Napa', strength: '500 mg', dosageForm: 'Tablet' }, expect: { matchedField: 'name', matchQuality: 'exact', matchScore: 1 } },
  { label: 'strength unit spacing is equivalent (500mg vs 500 mg)', query: 'napa 500mg', item: { name: 'Napa', strength: '500 mg', dosageForm: 'Tablet' }, expect: { matchedField: 'name', matchQuality: 'exact', matchScore: 1 } },
  { label: 'a bare number stays with the name when the row has no strength', query: 'nurofen 200', item: { name: 'Nurofen 200' }, expect: { matchedField: 'name', matchQuality: 'exact', matchScore: 1 } },
  { label: 'a bare number cannot stand in for a different strength', query: 'napa 500', item: { name: 'Napa', strength: '650 mg' }, expect: null },
  { label: 'a mismatched strength excludes the row', query: 'napa 650', item: { name: 'Napa', strength: '500 mg' }, expect: null },
  { label: 'a mismatched dosage form excludes the row', query: 'napa syrup', item: { name: 'Napa', dosageForm: 'Tablet' }, expect: null },
  { label: 'strength alone is a supporting result', query: '500 mg', item: { name: 'Napa', strength: '500 mg' }, expect: { matchedField: 'strength', matchQuality: 'supporting', matchScore: 1 } },
  { label: 'dosage form alone is a supporting result', query: 'tablet', item: { name: 'Napa', dosageForm: 'Tablet' }, expect: { matchedField: 'dosageForm', matchQuality: 'supporting', matchScore: 1 } },
  { label: 'a multiword dosage form is consumed as one phrase', query: 'chlorpheniramine oral solution', item: { name: 'Chlorpheniramine', dosageForm: 'Oral Solution' }, expect: { matchedField: 'name', matchQuality: 'exact', matchScore: 1 } },
  { label: 'a custom product matches by its local name', query: 'honey cough mix', item: { name: 'Honey Cough Mix' }, expect: { matchedField: 'name', matchQuality: 'exact', matchScore: 1 } },
];

describe('normalizeMedicineText', () => {
  it('folds case, collapses whitespace and punctuation', () => {
    expect(normalizeMedicineText('  Napa—Extra!  ')).toBe('napa-extra');
  });

  it('treats 500mg and 500 mg as the same strength', () => {
    expect(normalizeMedicineText('Napa 500 mg')).toBe(normalizeMedicineText('Napa 500mg'));
  });

  it('normalizes unicode dashes to hyphens', () => {
    expect(normalizeMedicineText('Alpha‑D3')).toBe('alpha-d3');
  });
});

describe('medicineEditDistance', () => {
  it('counts a substitution as one edit', () => {
    expect(medicineEditDistance('omeprazle', 'omeprazole')).toBe(1);
  });

  it('counts an adjacent transposition as one edit', () => {
    expect(medicineEditDistance('paracetmaol', 'paracetamol')).toBe(1);
  });

  it('counts an omission as one edit', () => {
    expect(medicineEditDistance('npa', 'napa')).toBe(1);
  });
});

describe('matchMedicineText golden vectors', () => {
  for (const vector of GOLDEN) {
    it(vector.label, () => {
      const match = matchMedicineText(vector.item, vector.query);
      if (vector.expect === null) {
        expect(match).toBeNull();
        return;
      }
      expect(match).not.toBeNull();
      expect(match!.matchedField).toBe(vector.expect.matchedField);
      expect(match!.matchQuality).toBe(vector.expect.matchQuality);
      if (vector.expect.matchScore !== undefined) {
        expect(match!.matchScore).toBeCloseTo(vector.expect.matchScore, 12);
      }
    });
  }
});

const ranked = (
  item: MedicineSearchValue,
  rank: number,
  matchScore = 1,
): { item: MedicineSearchValue; matchedField: 'name'; matchQuality: 'exact' | 'fuzzy'; matchedText: string; matchScore: number; rank: number } => ({
  item,
  matchedField: 'name',
  matchQuality: rank >= 8 ? 'fuzzy' : 'exact',
  matchedText: item.name,
  matchScore,
  rank,
});

describe('groupMedicineMatches', () => {
  const beximco = ranked({ name: 'Napa', manufacturer: 'Beximco', manufacturerId: 'm-1', dosageForm: 'Tablet' }, 2);
  const square = ranked({ name: 'Napa Extend', manufacturer: 'Square', manufacturerId: 'm-2', dosageForm: 'Tablet' }, 5, 0.5);
  const custom = ranked({ name: 'Honey Cough Mix' }, 2);

  it('orders manufacturers by their best contained match, not by name', () => {
    const groups = groupMedicineMatches([square, beximco]);
    expect(groups.map((group) => group.label)).toEqual(['Beximco', 'Square']);
  });

  it('reports counts and nests dosage groups, best-first', () => {
    const syrup = ranked({ name: 'Napa Syrup', manufacturer: 'Beximco', manufacturerId: 'm-1', dosageForm: 'Syrup' }, 6, 0.4);
    const groups = groupMedicineMatches([syrup, beximco]);
    expect(groups[0]!.count).toBe(2);
    expect(groups[0]!.dosageGroups.map((group) => group.label)).toEqual(['Tablet', 'Syrup']);
    expect(groups[0]!.dosageGroups[0]!.items.map((match) => match.item.name)).toEqual(['Napa']);
  });

  it('falls back to "Custom / manufacturer not set" and "Dosage form not set"', () => {
    const groups = groupMedicineMatches([custom]);
    expect(groups[0]!.label).toBe('Custom / manufacturer not set');
    expect(groups[0]!.dosageGroups[0]!.label).toBe('Dosage form not set');
  });

  it('does not push the fallback group below a worse named group', () => {
    // The fallback group holds the exact match here, so it leads -- the label is
    // a fact about the data, not a sorting key.
    const groups = groupMedicineMatches([square, custom]);
    expect(groups[0]!.label).toBe('Custom / manufacturer not set');
  });

  it('sorts within a dosage group by rank, then score, then name', () => {
    const a = ranked({ name: 'Acal', manufacturerId: 'm-1' }, 8, 0.7);
    const b = ranked({ name: 'Zcal', manufacturerId: 'm-1' }, 8, 0.9);
    const c = ranked({ name: 'Bcal', manufacturerId: 'm-1' }, 8, 0.7);
    expect(groupMedicineMatches([a, b, c])[0]!.dosageGroups[0]!.items.map((match) => match.item.name)).toEqual(['Zcal', 'Acal', 'Bcal']);
  });
});

describe('describeMedicineMatch', () => {
  it('names the tier a cashier can act on', () => {
    expect(describeMedicineMatch({ matchedField: 'name', matchQuality: 'exact' })).toBe('Exact brand match');
    expect(describeMedicineMatch({ matchedField: 'genericName', matchQuality: 'exact' })).toBe('Exact generic match');
    expect(describeMedicineMatch({ matchedField: 'name', matchQuality: 'fuzzy' })).toBe('Closest brand match');
    expect(describeMedicineMatch({ matchedField: 'genericName', matchQuality: 'fuzzy' })).toBe('Closest generic match');
    expect(describeMedicineMatch({ matchedField: 'barcode', matchQuality: 'exact' })).toBe('Exact barcode');
    expect(describeMedicineMatch({ matchedField: 'sku', matchQuality: 'exact' })).toBe('Exact SKU');
    expect(describeMedicineMatch({ matchedField: 'strength', matchQuality: 'supporting' })).toBe('Matched by strength');
  });
});

describe('medicineMatchRank', () => {
  it('assigns the server tier order to API rows', () => {
    expect(medicineMatchRank('barcode', 'exact')).toBeLessThan(medicineMatchRank('sku', 'exact'));
    expect(medicineMatchRank('sku', 'exact')).toBeLessThan(medicineMatchRank('name', 'exact'));
    expect(medicineMatchRank('name', 'exact')).toBeLessThan(medicineMatchRank('genericName', 'exact'));
    expect(medicineMatchRank('genericName', 'exact')).toBeLessThan(medicineMatchRank('alias', 'exact'));
    expect(medicineMatchRank('alias', 'exact')).toBeLessThan(medicineMatchRank('name', 'partial'));
    expect(medicineMatchRank('name', 'partial')).toBeLessThan(medicineMatchRank('genericName', 'partial'));
    expect(medicineMatchRank('genericName', 'partial')).toBeLessThan(medicineMatchRank('alias', 'partial'));
    expect(medicineMatchRank('alias', 'partial')).toBeLessThan(medicineMatchRank('name', 'fuzzy'));
    expect(medicineMatchRank('name', 'fuzzy')).toBeLessThan(medicineMatchRank('genericName', 'fuzzy'));
    expect(medicineMatchRank('genericName', 'fuzzy')).toBeLessThan(medicineMatchRank('strength', 'supporting'));
  });
});

describe('medicineMatchesAreFuzzy', () => {
  it('is true only when every result is a typo guess', () => {
    expect(medicineMatchesAreFuzzy([{ matchQuality: 'fuzzy' }, { matchQuality: 'fuzzy' }])).toBe(true);
    expect(medicineMatchesAreFuzzy([{ matchQuality: 'exact' }, { matchQuality: 'fuzzy' }])).toBe(false);
    expect(medicineMatchesAreFuzzy([])).toBe(false);
  });
});

describe('highlightMedicineSpans', () => {
  it('marks literal query tokens, case-insensitively', () => {
    expect(highlightMedicineSpans('Napa Extra', 'napa ex')).toEqual([
      { text: 'Napa', hit: true },
      { text: ' ', hit: false },
      { text: 'Ex', hit: true },
      { text: 'tra', hit: false },
    ]);
  });

  it('marks nothing when no token occurs', () => {
    expect(highlightMedicineSpans('Omeprazole', 'napa')).toEqual([{ text: 'Omeprazole', hit: false }]);
  });

  it('merges overlapping tokens into one span', () => {
    expect(highlightMedicineSpans('Paracetamol', 'paracetamol ace')).toEqual([{ text: 'Paracetamol', hit: true }]);
  });

  it('returns the whole text unmarked for a blank query', () => {
    expect(highlightMedicineSpans('Napa', '   ')).toEqual([{ text: 'Napa', hit: false }]);
  });
});

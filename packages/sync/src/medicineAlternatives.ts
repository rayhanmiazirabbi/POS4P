import { normalizeMedicineText, type MedicineSearchValue } from './medicineSearch';

/**
 * A medicine that can be asked for alternatives: enough identity to exclude
 * itself (`id`) and everything the generic comparison reads. Structural, so
 * `ShelfProduct` and any server row satisfy it without this package depending
 * on the client that fetched them (same rationale as `ShelfSource`).
 */
export type MedicineAlternativeSource = MedicineSearchValue & { id: string; dosageFormId?: string | null };

/**
 * One substitutable brand, with what makes it substitutable.
 *
 * `tier` is how close the substitute is, in steps a pharmacist actually weighs:
 * 0 is the same strength in the same form, 1 the same strength in another form,
 * 2 anything else on the same generic -- usually a different strength, which is
 * a conversation with the customer rather than a swap.
 */
export type MedicineAlternative<T> = {
  item: T;
  tier: 0 | 1 | 2;
  sameStrength: boolean;
  sameDosageForm: boolean;
};

function sameGeneric(left: string | null | undefined, right: string | null | undefined): boolean {
  const a = normalizeMedicineText(left ?? '');
  const b = normalizeMedicineText(right ?? '');
  return a !== '' && a === b;
}

/**
 * Other brands of the same generic, best swap first.
 *
 * Equality is on the whole normalized generic string, shared with the search
 * matcher so `Paracetamol + Caffeine` never answers plain `Paracetamol`: a
 * combination is not substitutable with a single agent, and a list that quietly
 * offered it would be read as confirmed equivalents. Ingredient-level matching
 * is the later, harder version of this.
 *
 * Not grouped by manufacturer the way search results are. A search asks "what
 * did the cashier mean" and the manufacturer hierarchy narrows that; this asks
 * "what else is there" and brands compare best side by side, where the groups
 * would be singletons with a header each.
 */
export function findMedicineAlternatives<T extends MedicineAlternativeSource>(
  items: readonly T[],
  target: MedicineAlternativeSource,
): readonly MedicineAlternative<T>[] {
  if (normalizeMedicineText(target.genericName ?? '') === '') return [];

  const targetStrength = normalizeMedicineText(target.strength ?? '');
  const targetForm = normalizeMedicineText(target.dosageForm ?? '');

  const alternatives: MedicineAlternative<T>[] = [];
  for (const item of items) {
    if (item.id === target.id) continue;
    if (!sameGeneric(item.genericName, target.genericName)) continue;

    // A strength is "the same" only when both rows state one and they agree
    // after normalization ("500 mg" is "500mg"). A row with no strength at all
    // is not the same strength -- it is an unknown, and unknowns sort with the
    // different strengths where a person reads the label.
    const strength = normalizeMedicineText(item.strength ?? '');
    const sameStrength = targetStrength !== '' && strength !== '' && strength === targetStrength;
    const sameDosageForm =
      targetForm !== '' && normalizeMedicineText(item.dosageForm ?? '') === targetForm
        ? true
        : targetForm === '' && target.dosageFormId != null && item.dosageFormId === target.dosageFormId;
    const tier: 0 | 1 | 2 = sameStrength ? (sameDosageForm ? 0 : 1) : 2;
    alternatives.push({ item, tier, sameStrength, sameDosageForm });
  }

  const sameManufacturer = (item: MedicineSearchValue): boolean =>
    (target.manufacturerId != null && item.manufacturerId === target.manufacturerId) ||
    sameGeneric(item.manufacturer, target.manufacturer);

  return alternatives.sort((left, right) => {
    if (left.tier !== right.tier) return left.tier - right.tier;
    // The question is "which other brand"; the target's own house line answered
    // it already, so within a tier the familiar brand goes last.
    const leftHome = sameManufacturer(left.item) ? 1 : 0;
    const rightHome = sameManufacturer(right.item) ? 1 : 0;
    if (leftHome !== rightHome) return leftHome - rightHome;
    return left.item.name.localeCompare(right.item.name) || left.item.id.localeCompare(right.item.id);
  });
}

/**
 * A catalogue alternative row, as far as merging needs to know it. Structural
 * for the same reason as `MedicineAlternativeSource`.
 */
export type CatalogAlternativeLike = { storeProductId?: string | null };

/**
 * Catalogue rows the shelf section has not already shown.
 *
 * A catalogue alternative that is on this shelf carries the shelf row's id in
 * `storeProductId`, so the shelf section either listed it as an alternative or
 * it *is* the row the cashier asked about -- either way it is not news. Pass
 * the target plus the shelf alternatives; anything else in the catalogue
 * answer is a brand this branch does not stock, which is the part the shelf
 * could never say.
 */
export function mergeMedicineAlternatives<T extends CatalogAlternativeLike>(
  onShelf: readonly { id: string }[],
  catalog: readonly T[],
): readonly T[] {
  const shelfIds = new Set(onShelf.map((row) => row.id));
  return catalog.filter((row) => row.storeProductId == null || !shelfIds.has(row.storeProductId));
}

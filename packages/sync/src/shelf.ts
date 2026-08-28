import { normalizeBarcode } from '@pharmacy/core';

import type { OutboxStorage } from './outbox';
import { groupMedicineMatches, matchMedicineText, type MedicineManufacturerGroup, type MedicineMatchQuality, type RankedMedicine } from './medicineSearch';

/**
 * The shelf, kept on the device so a counter can still sell without a network.
 *
 * All three shells named a `cache` state variable and then filled it from a live
 * `GET /products/current` on every mount, keeping nothing. That works until the
 * moment it matters: a phone that starts up in a back room with no signal shows an
 * empty shelf, so there is nothing to add to a cart, so the offline outbox beneath
 * it -- the whole reason this platform queues sales -- never sees a single one. The
 * cache was in memory, which is to say it was not a cache.
 *
 * Kept deliberately small. It holds what a cart line needs, plus the last known
 * available balance so the POS can distinguish an out-of-stock shelf row from an
 * unadopted catalogue result. The server remains authoritative at checkout.
 */
export type ShelfProduct = {
  id: string;
  sku: string;
  /** What the cashier reads. A list of bare SKUs is a list picked from memory. */
  name: string;
  /** What a scanner reads. Null for a line that has never been barcoded. */
  barcode: string | null;
  /** Decimal serialized as a fixed-cents string, matching `StoreProduct.salePrice`. */
  salePrice: string;
  rack: string | null;
  genericName?: string | null;
  strength?: string | null;
  manufacturerId?: string | null;
  manufacturer?: string | null;
  dosageFormId?: string | null;
  dosageForm?: string | null;
  unit?: string;
  availableQuantity?: string;
};

/**
 * A cached shelf, stamped with the branch it belongs to.
 *
 * `storeId` is the important field. A till that changes branch -- or a phone whose
 * user switches workspace -- must not serve the previous branch's shelf, because
 * the SKUs may exist in both at different prices and the wrong one is a wrong
 * receipt. `readShelf` refuses a mismatch rather than filtering it.
 */
export type ShelfCache = {
  storeId: string;
  /** When the shelf was last fetched, for telling a cashier how old the prices are. */
  fetchedAt: string;
  products: readonly ShelfProduct[];
};

/**
 * What `save` accepts: the subset of `StoreProduct` a shelf keeps.
 *
 * Structural rather than an import of `@pharmacy/api`. This package is the offline
 * layer beneath the HTTP client -- the outbox holds sales that have no client to
 * post them yet -- and depending upwards on the client to name three fields would
 * invert that. A `StoreProduct` satisfies this shape as it stands, so callers pass
 * the API rows straight through.
 */
export type ShelfSource = {
  id: string; sku: string; name: string; salePrice: string;
  barcode?: string | null; rack?: string | null; genericName?: string | null;
  strength?: string | null; manufacturerId?: string | null; manufacturer?: string | null;
  dosageFormId?: string | null; dosageForm?: string | null;
  unit?: string; availableQuantity?: string;
};

/** Narrowed from the API row, dropping what a cart does not need. */
export function toShelfProduct(product: ShelfSource): ShelfProduct {
  return {
    id: product.id,
    sku: product.sku,
    name: product.name,
    barcode: product.barcode ?? null,
    salePrice: product.salePrice,
    rack: product.rack ?? null,
    genericName: product.genericName ?? null,
    strength: product.strength ?? null,
    manufacturerId: product.manufacturerId ?? null,
    manufacturer: product.manufacturer ?? null,
    dosageFormId: product.dosageFormId ?? null,
    dosageForm: product.dosageForm ?? null,
    unit: product.unit ?? 'unit',
    ...(product.availableQuantity === undefined ? {} : { availableQuantity: product.availableQuantity }),
  };
}

function isShelfProduct(value: unknown): value is ShelfProduct {
  if (typeof value !== 'object' || value === null) return false;
  const row = value as Record<string, unknown>;
  return (
    typeof row.id === 'string' &&
    row.id !== '' &&
    typeof row.sku === 'string' &&
    typeof row.name === 'string' &&
    typeof row.salePrice === 'string'
  );
}

/**
 * Decode a stored shelf, or answer `null` if it cannot be trusted.
 *
 * Unlike the outbox -- which throws on an unreadable blob, because silently
 * discarding paid-for sales is worse than an error -- a corrupt shelf reads as
 * absent. There is nothing here that is not also on the server, so the cost of
 * dropping it is one fetch.
 */
function decode(raw: string | null): ShelfCache | null {
  if (raw === null) return null;
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return null;
  }
  if (typeof parsed !== 'object' || parsed === null) return null;
  const row = parsed as Record<string, unknown>;
  if (typeof row.storeId !== 'string' || row.storeId === '' || typeof row.fetchedAt !== 'string') return null;
  if (!Array.isArray(row.products) || !row.products.every(isShelfProduct)) return null;
  return { storeId: row.storeId, fetchedAt: row.fetchedAt, products: row.products.map((product) => toShelfProduct(product as ShelfSource)) };
}

/** How a shell reads the shelf it has, and how stale it is. */
export type ShelfRead =
  | { status: 'empty' }
  /** The cached shelf belongs to another branch; it must not be shown. */
  | { status: 'other-branch'; cachedStoreId: string }
  | { status: 'cached'; products: readonly ShelfProduct[]; fetchedAt: string; ageMs: number };

/**
 * Read the cached shelf for one branch.
 *
 * `storeId` is required rather than optional: answering "whatever was cached" is
 * how the wrong branch's prices would reach a receipt.
 *
 * The age is reported, not judged. There is no expiry here, and that is the
 * point -- a shelf three days old is the difference between selling and not
 * selling when the line is out the door, and a stale price is a smaller error than
 * a refused sale. The shell shows the age and lets the cashier decide.
 *
 * Availability is a last-known hint, not a reservation. It changes with every
 * sale on every till, so the server still allocates batches at replay and an
 * offline sale can come back `INSUFFICIENT_STOCK`. Old cache rows without the
 * hint remain sellable until the next successful refresh adds it.
 */
export function readShelf(cache: ShelfCache | null, storeId: string, nowUtcIso: string): ShelfRead {
  if (cache === null) return { status: 'empty' };
  if (cache.storeId !== storeId) return { status: 'other-branch', cachedStoreId: cache.storeId };
  const age = Date.parse(nowUtcIso) - Date.parse(cache.fetchedAt);
  return {
    status: 'cached',
    products: cache.products,
    fetchedAt: cache.fetchedAt,
    // A clock moved backwards must not read as a shelf from the future.
    ageMs: Number.isFinite(age) ? Math.max(0, age) : 0,
  };
}

/** The shelf store a shell binds to its own platform storage. */
export type ShelfStore = {
  /** The shelf held for `storeId`, or why there is none to show. */
  read(storeId: string, nowUtcIso?: string): Promise<ShelfRead>;
  /** Replace the shelf after a successful fetch. */
  save(storeId: string, products: readonly ShelfSource[], nowUtcIso?: string): Promise<ShelfCache>;
  clear(): Promise<void>;
};

/**
 * A shelf cache over the same `OutboxStorage` shape the outbox uses, so each shell
 * supplies one key from the storage it already has -- Tauri's store, Dexie, or
 * expo-sqlite -- and no shell grows a second persistence layer.
 */
export function createShelfStore(storage: OutboxStorage): ShelfStore {
  return {
    async read(storeId, nowUtcIso = new Date().toISOString()) {
      let raw: string | null;
      try {
        raw = await storage.read();
      } catch {
        // Storage itself is unavailable. Same answer as a corrupt blob: the shelf
        // is fetchable, and a counter that throws here has no shelf either way.
        return { status: 'empty' };
      }
      return readShelf(decode(raw), storeId, nowUtcIso);
    },

    async save(storeId, products, nowUtcIso = new Date().toISOString()) {
      const cache: ShelfCache = { storeId, fetchedAt: nowUtcIso, products: products.map(toShelfProduct) };
      try {
        await storage.write(JSON.stringify(cache));
      } catch {
        // A shelf that could not be written is a shelf that has to be fetched next
        // time. The caller has the products in hand right now and is about to sell
        // from them, so failing the call would break a working counter to report a
        // degraded one.
      }
      return cache;
    },

    async clear() {
      try {
        await storage.write(JSON.stringify({ storeId: '', fetchedAt: '', products: [] }));
      } catch {
        // Nothing to do and nothing at risk: an unreadable shelf reads as empty.
      }
    },
  };
}

/**
 * How old the prices are, in words a cashier can act on.
 *
 * Rounded down and coarse on purpose. The number is not a measurement, it is a
 * prompt to check a price before quoting it, and "3 h" answers that as well as
 * "3 h 14 m" while surviving a device clock that is a few minutes out.
 */
export function describeShelfAge(ageMs: number): string {
  const minutes = Math.floor(ageMs / 60_000);
  if (minutes < 1) return 'just now';
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours} h ago`;
  const days = Math.floor(hours / 24);
  return days === 1 ? 'yesterday' : `${days} days ago`;
}

/** What a counter has to show, after trying the network and falling back to disk. */
export type ShelfLoad =
  /** Straight from the server. */
  | { status: 'fresh'; products: readonly ShelfProduct[] }
  /** Off the device because the fetch failed. Show `note` -- the prices may have moved. */
  | { status: 'stale'; products: readonly ShelfProduct[]; ageMs: number; note: string }
  /** No shelf from either source. `reason` is the fetch failure, which is the actionable one. */
  | { status: 'unavailable'; reason: string };

/**
 * Load the shelf for one branch: cache first, then the network.
 *
 * The policy this exists to state once is the last branch below -- **a failed fetch
 * with a cache behind it is not an error.** Each shell used to write its own
 * `.catch(() => setError(...))` around this call, so a phone that lost signal
 * reported "Could not load the shelf" and emptied a list it was perfectly able to
 * fill. That is the moment the offline outbox is for, and it was the one moment
 * nothing could reach it.
 *
 * `onCached` fires before the fetch is awaited, so a device that starts up with no
 * signal paints a usable shelf immediately instead of after a connection timeout.
 * It is called at most once, and not at all when there is nothing cached.
 */
export async function loadShelf(
  store: ShelfStore,
  storeId: string,
  fetch: () => Promise<readonly ShelfSource[]>,
  onCached?: (products: readonly ShelfProduct[], ageMs: number) => void,
): Promise<ShelfLoad> {
  // Read before fetching, not as a fallback afterwards: the point is to have
  // something on screen while the network is still deciding whether it exists.
  const cached = await store.read(storeId);
  if (cached.status === 'cached' && onCached !== undefined) onCached(cached.products, cached.ageMs);

  try {
    const products = await fetch();
    return { status: 'fresh', products: (await store.save(storeId, products)).products };
  } catch (cause) {
    const reason = cause instanceof Error ? cause.message : 'Could not load the shelf';
    // A cache for another branch is deliberately not offered here. It would be a
    // plausible shelf at the wrong prices, which is worse than no shelf at all.
    if (cached.status !== 'cached') return { status: 'unavailable', reason };
    return {
      status: 'stale',
      products: cached.products,
      ageMs: cached.ageMs,
      note: `Offline — prices last updated ${describeShelfAge(cached.ageMs)}. Check anything unusual before quoting it.`,
    };
  }
}

/**
 * How a shelf row was matched, so a screen can act on the difference.
 *
 * `barcode` is the only one a counter should ever add to a cart unattended. The
 * others are a cashier's guess narrowed down, and adding the top guess without
 * showing it is how the wrong strength of the right medicine gets sold.
 */
export type ShelfMatchKind = 'barcode' | 'sku' | 'name' | 'genericName' | 'alias' | 'strength' | 'dosageForm';

export type ShelfMatch = {
  product: ShelfProduct;
  matchedBy: ShelfMatchKind;
  matchQuality: MedicineMatchQuality;
  matchedText: string;
  matchScore: number;
  rank: number;
};

/**
 * Find shelf rows for something typed, scanned, or read by a camera.
 *
 * One function for all three inputs because a hardware barcode gun *is* the
 * keyboard: it types the digits into the search box and presses Enter. The
 * desktop till and the web counter therefore get scanning from this with no
 * camera and no new dependency, and the phone's camera feeds the same call.
 *
 * Ordering is by how sure the match is, and the gap between the first two is the
 * important one. A barcode is an identifier -- it either is that product or it is
 * not -- so it wins outright and `scanShelf` will act on it. Everything below is a
 * suggestion for a person to confirm.
 */
export function matchShelf(products: readonly ShelfProduct[], query: string): ShelfMatch[] {
  const raw = query.trim();
  if (raw === '') return [];
  const scanned = normalizeBarcode(raw);
  const needle = raw.toLocaleLowerCase();
  const matches: ShelfMatch[] = [];

  for (const product of products) {
    // Compared normalized on both sides: some scanners emit a trailing space and
    // some catalogues are keyed in with the group separators left in.
    if (product.barcode !== null && normalizeBarcode(product.barcode) === scanned) {
      matches.push({ product, matchedBy: 'barcode', matchQuality: 'exact', matchedText: product.barcode, matchScore: 1, rank: 0 });
      continue;
    }
    const sku = product.sku.toLocaleLowerCase();
    if (sku === needle) {
      matches.push({ product, matchedBy: 'sku', matchQuality: 'exact', matchedText: product.sku, matchScore: 1, rank: 1 });
      continue;
    }
    const textMatch = matchMedicineText(product, raw);
    if (textMatch !== null) {
      matches.push({ product, matchedBy: textMatch.matchedField, ...textMatch });
      continue;
    }
    if (sku.includes(needle)) {
      matches.push({ product, matchedBy: 'sku', matchQuality: 'partial', matchedText: product.sku, matchScore: needle.length / sku.length, rank: 7 });
    }
  }

  return matches.sort(
    (a, b) => a.rank - b.rank || b.matchScore - a.matchScore || a.product.name.localeCompare(b.product.name),
  );
}

export function groupShelfMatches(matches: readonly ShelfMatch[]): readonly MedicineManufacturerGroup<ShelfProduct>[] {
  return groupMedicineMatches(matches.map((match) => ({
    item: match.product,
    matchedField: match.matchedBy === 'sku' || match.matchedBy === 'barcode' ? 'name' : match.matchedBy,
    matchQuality: match.matchQuality,
    matchedText: match.matchedText,
    matchScore: match.matchScore,
    rank: match.rank,
  })));
}

/** What a counter renders for an active query: groups, rows in order, matches. */
export type GroupedShelfView = {
  matches: readonly ShelfMatch[];
  groups: readonly MedicineManufacturerGroup<ShelfProduct>[];
  flatRows: readonly RankedMedicine<ShelfProduct>[];
};

/**
 * Build the grouped view of an active search: manufacturer -> dosage-form
 * groups, plus the rows flattened in ranked order for arrow-key traversal.
 * `null` when no query is active -- the untouched box keeps each shell's
 * existing blank-shelf presentation, and a cap preserves a shell's existing
 * result limit by slicing the matches before they are grouped.
 */
export function buildGroupedShelfView(
  products: readonly ShelfProduct[],
  query: string,
  cap?: number,
): GroupedShelfView | null {
  if (query.trim() === '') return null;
  const matches = cap === undefined ? matchShelf(products, query) : matchShelf(products, query).slice(0, cap);
  const groups = groupShelfMatches(matches);
  const flatRows = groups.flatMap((manufacturer) => manufacturer.dosageGroups.flatMap((dosage) => dosage.items));
  return { matches, groups, flatRows };
}

/** What a scan resolved to, and whether the counter may act on it without asking. */
export type ShelfScan =
  /** Exactly one barcode match. Safe to add to the cart directly. */
  | { status: 'product'; product: ShelfProduct }
  /** Nothing on this device carries that code. */
  | { status: 'unknown'; scanned: string }
  /** Two rows share the code, or it only matched by name. A person has to choose. */
  | { status: 'ambiguous'; options: readonly ShelfMatch[] };

/**
 * Resolve a scanned code against the cached shelf.
 *
 * Separate from `matchShelf` because a scan is not a search: the camera hands over
 * a code with no cashier watching the result, so this has to say plainly whether
 * the answer is certain. Only a single barcode hit is.
 *
 * `unknown` is a real and ordinary outcome, not an error -- a line that has never
 * been barcoded, or a code that belongs to another branch's catalogue. The screen
 * should offer the search box, not a failure.
 */
export function scanShelf(products: readonly ShelfProduct[], scanned: string): ShelfScan {
  const code = normalizeBarcode(scanned);
  if (code === '') return { status: 'unknown', scanned: code };
  const matches = matchShelf(products, code);
  const exact = matches.filter((match) => match.matchedBy === 'barcode');
  const sole = exact.length === 1 ? exact[0] : undefined;
  if (sole !== undefined) return { status: 'product', product: sole.product };
  // Two shelf rows sharing a barcode should not happen -- the backend has a unique
  // constraint on (organization, barcode) -- but it is one query away from being
  // possible and guessing between them would sell the wrong one at the wrong price.
  if (exact.length > 1) return { status: 'ambiguous', options: exact };
  return matches.length === 0 ? { status: 'unknown', scanned: code } : { status: 'ambiguous', options: matches };
}

/**
 * Resolve a submitted search box, where a cashier is watching the result.
 *
 * Deliberately more trusting than `scanShelf`, and only by one step: a single
 * *exact* SKU match counts as certain too, because a SKU is an identifier and
 * somebody chose to type all of it. That is the keyboard till's whole workflow --
 * type `PARA-500`, press Enter, next customer.
 *
 * What it still refuses is the substring. The desktop counter used to add
 * `matches[0]` on Enter, which was survivable while the search only looked at SKUs
 * and became a hazard the moment it looked at names: a cashier typing "para" and
 * pressing Enter would have sold whichever of Paracetamol 500mg and 650mg happened
 * to sort first. A partial match narrows the list; it does not pick from it.
 */
export function submitShelfEntry(products: readonly ShelfProduct[], entry: string): ShelfScan {
  const scan = scanShelf(products, entry);
  if (scan.status === 'product') return scan;
  const skus = matchShelf(products, entry).filter((match) => match.matchedBy === 'sku' && match.matchQuality === 'exact');
  const sole = skus.length === 1 ? skus[0] : undefined;
  return sole === undefined ? scan : { status: 'product', product: sole.product };
}

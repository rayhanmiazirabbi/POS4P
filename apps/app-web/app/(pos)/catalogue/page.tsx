'use client';

import { useQuery, useQueryClient } from '@tanstack/react-query';
import type { CatalogAlternativeItem, CatalogSearchItem, PurchaseOrder } from '@pharmacy/api';
import { colors, spacing, tokens } from '@pharmacy/design-tokens';
import {
  describeMedicineMatch,
  groupMedicineMatches,
  highlightMedicineSpans,
  medicineMatchesAreFuzzy,
} from '@pharmacy/sync';
import { can } from '@pharmacy/permissions';
import { useRouter } from 'next/navigation';
import { useEffect, useMemo, useRef, useState, type CSSProperties, type KeyboardEvent, type ReactNode } from 'react';
import { z } from 'zod';

import { pharmacyApi } from '@/lib/api';
import { CATALOGUE_PAGE_SIZE, hasMoreResults, nextResultWindow, toRanked } from '@/lib/catalogueRows';
import { decimalEntry } from '@/lib/numeric-input';
import { useSession } from '@/lib/session';
import { decimalAmount, fieldIssue, positiveQuantity } from '@/lib/validation';

const card: CSSProperties = { background: colors.surface, border: `1px solid ${colors.border}`, borderRadius: 12, padding: spacing.lg };
const input: CSSProperties = { padding: spacing.sm, borderRadius: 8, border: `1px solid ${colors.border}` };
const button: CSSProperties = { ...input, cursor: 'pointer', background: colors.primary, color: colors.primaryForeground, border: 'none', fontWeight: tokens.typography.weights.medium };
const quietButton: CSSProperties = { ...input, cursor: 'pointer', background: colors.surface };

const STATUS_STYLE: Record<CatalogSearchItem['shopStatus'], CSSProperties> = {
  on_shelf: { color: colors.success },
  in_org: { color: colors.warning },
  absent: { color: colors.muted },
};

/** The server refuses an unpriced adoption, so the form says so first. */
const shelfFormSchema = z.object({
  sku: z.string().trim().min(1, 'A shelf row needs a SKU'),
  price: decimalAmount,
});

/** Adoption may leave the SKU blank: the server generates a deterministic one. */
const adoptFormSchema = z.object({ price: decimalAmount });

const entryFormSchema = z.object({
  name: z.string().trim().min(1, 'A catalogue entry needs a name'),
  packageUnit: z.string().trim().min(1, 'Enter a package unit, e.g. tablet'),
  price: decimalAmount,
});

function useDebounced(value: string, delayMs: number): string {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const timer = setTimeout(() => setDebounced(value), delayMs);
    return () => clearTimeout(timer);
  }, [value, delayMs]);
  return debounced;
}

function rowKey(row: CatalogSearchItem): string {
  return `cat:${row.catalogProductId ?? '-'}|org:${row.pharmacyProductId ?? '-'}`;
}

export default function CataloguePage(): ReactNode {
  const router = useRouter();
  const queryClient = useQueryClient();
  const { user } = useSession();
  const role = user?.role ?? null;
  const mayAdopt = role !== null && can(role, 'products.adopt');
  const mayManageShelf = role !== null && can(role, 'store.manage');

  const [query, setQuery] = useState('');
  const searchTerm = useDebounced(query.trim(), 250);
  // 50 rows per page, extended by "Show more results": the server keeps one
  // deterministic order, so widening the window appends later pages into the
  // groups already on screen instead of rebuilding them.
  const [resultWindow, setResultWindow] = useState(CATALOGUE_PAGE_SIZE);
  const searchRef = useRef<HTMLInputElement>(null);
  const rowRefs = useRef<(HTMLLIElement | null)[]>([]);

  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [panelAction, setPanelAction] = useState<'shelf' | 'po' | 'alt'>('po');
  const [sku, setSku] = useState('');
  const [price, setPrice] = useState('');
  const [quantity, setQuantity] = useState('10');
  const [selectedPoId, setSelectedPoId] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  // Owner-only form for a medicine nobody stocks yet: the catalogue entry is
  // created first (global reference data), then adopted onto this shop.
  const [entryName, setEntryName] = useState('');
  const [entryGeneric, setEntryGeneric] = useState('');
  const [entryStrength, setEntryStrength] = useState('');
  const [entryUnit, setEntryUnit] = useState('tablet');
  const [entryRx, setEntryRx] = useState(false);
  const [entryPrice, setEntryPrice] = useState('');

  const searchQuery = useQuery({
    queryKey: ['catalogue', 'search', searchTerm, resultWindow],
    queryFn: async () =>
      await pharmacyApi.products.search({ q: searchTerm }, { limit: resultWindow }),
    enabled: searchTerm.length > 0,
    staleTime: 15_000,
  });
  const draftOrdersQuery = useQuery({
    queryKey: ['purchasing', 'purchase-orders', 'draft'],
    queryFn: async () =>
      (await pharmacyApi.purchaseOrders.list({ status: 'draft' }, { limit: 50 })).items,
    staleTime: 30_000,
  });

  const rows = searchQuery.data?.items ?? [];
  const total = searchQuery.data?.total ?? 0;
  const draftOrders = draftOrdersQuery.data ?? [];
  const searched = searchTerm.length > 0;
  // The client derives the manufacturer -> dosage-form groups from ranked rows;
  // the response stays flat and paginated for every other consumer.
  const groups = useMemo(
    () => groupMedicineMatches(rows.map(toRanked)),
    [rows],
  );
  const flatRows = useMemo(
    () => groups.flatMap((manufacturer) => manufacturer.dosageGroups.flatMap((dosage) => dosage.items.map((entry) => entry.item))),
    [groups],
  );
  const rowIndex = useMemo(() => new Map(flatRows.map((row, index) => [row, index])), [flatRows]);

  useEffect(() => {
    // A new search starts from the first page again.
    setResultWindow(CATALOGUE_PAGE_SIZE);
  }, [searchTerm]);

  /** Arrow traversal walks medicine rows only, skipping the group headings. */
  function focusRow(index: number): void {
    const count = flatRows.length;
    if (count === 0) return;
    const next = ((index % count) + count) % count;
    rowRefs.current[next]?.focus();
  }

  function onRowKeyDown(event: KeyboardEvent<HTMLLIElement>, index: number): void {
    if (event.key === 'ArrowDown') {
      event.preventDefault();
      focusRow(index + 1);
    } else if (event.key === 'ArrowUp') {
      event.preventDefault();
      if (index === 0) searchRef.current?.focus();
      else focusRow(index - 1);
    } else if (event.key === 'Escape') {
      searchRef.current?.focus();
    }
  }

  function resetFeedback(): void {
    setError(null);
    setNote(null);
  }

  async function invalidate(): Promise<void> {
    await queryClient.invalidateQueries({ queryKey: ['catalogue'] });
    await queryClient.invalidateQueries({ queryKey: ['purchasing', 'purchase-orders'] });
  }

  function expand(row: CatalogSearchItem, action: 'shelf' | 'po' | 'alt'): void {
    resetFeedback();
    const key = rowKey(row);
    if (expandedId === key && panelAction === action) {
      setExpandedId(null);
      return;
    }
    setExpandedId(key);
    setPanelAction(action);
    setSku(row.sku ?? '');
    setPrice(row.referenceUnitPrice ?? '');
    setQuantity('10');
  }

  async function adopt(row: CatalogSearchItem): Promise<void> {
    resetFeedback();
    const parsed = adoptFormSchema.safeParse({ price: price.trim() });
    if (!parsed.success || row.catalogProductId === null || row.catalogProductId === undefined) {
      setError(fieldIssue(parsed) ?? 'This row has no catalogue entry to adopt.');
      return;
    }
    setBusy(true);
    try {
      await pharmacyApi.products.adopt({
        catalogProductId: row.catalogProductId,
        salePrice: price.trim(),
        ...(sku.trim() === '' ? {} : { sku: sku.trim() }),
      });
      setNote(`${row.name} adopted onto this shelf.`);
      setExpandedId(null);
      await invalidate();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Could not adopt the product');
    } finally {
      setBusy(false);
    }
  }

  async function backOnShelf(row: CatalogSearchItem): Promise<void> {
    resetFeedback();
    if (row.pharmacyProductId === null || row.pharmacyProductId === undefined) return;
    const parsed = shelfFormSchema.safeParse({ sku: sku.trim(), price: price.trim() });
    if (!parsed.success) {
      setError(fieldIssue(parsed));
      return;
    }
    setBusy(true);
    try {
      await pharmacyApi.products.enableStoreProduct({
        pharmacyProductId: row.pharmacyProductId,
        sku: sku.trim(),
        salePrice: price.trim(),
      });
      setNote(`${row.name} is back on the shelf.`);
      setExpandedId(null);
      await invalidate();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Could not enable the product');
    } finally {
      setBusy(false);
    }
  }

  async function addToPurchaseOrder(row: CatalogSearchItem): Promise<void> {
    resetFeedback();
    const parsedQty = positiveQuantity.safeParse(quantity.trim());
    if (!parsedQty.success) {
      setError(fieldIssue(parsedQty));
      return;
    }
    setBusy(true);
    try {
      const link = {
        quantity: quantity.trim(),
        ...(row.catalogProductId ? { catalogProductId: row.catalogProductId } : {}),
        ...(row.pharmacyProductId ? { pharmacyProductId: row.pharmacyProductId } : {}),
      };
      if (selectedPoId === '') {
        const created = await pharmacyApi.purchaseOrders.create(
          { items: [{ name: row.name, ...link }] },
          {},
        );
        setNote(`Order ${created.data.id.slice(0, 8)} started with ${row.name}.`);
      } else {
        await pharmacyApi.purchaseOrders.addItem(selectedPoId, { name: row.name, ...link });
        setNote(`${row.name} added to the order.`);
      }
      setExpandedId(null);
      await invalidate();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Could not add to the purchase order');
    } finally {
      setBusy(false);
    }
  }

  async function createEntryAndAdopt(): Promise<void> {
    resetFeedback();
    const parsed = entryFormSchema.safeParse({ name: entryName, packageUnit: entryUnit, price: entryPrice.trim() });
    if (!parsed.success) {
      setError(fieldIssue(parsed));
      return;
    }
    setBusy(true);
    try {
      const created = await pharmacyApi.catalog.createProduct({
        name: entryName.trim(),
        countryCode: 'BD',
        packageUnit: entryUnit.trim(),
        ...(entryGeneric.trim() === '' ? {} : { genericName: entryGeneric.trim() }),
        ...(entryStrength.trim() === '' ? {} : { strength: entryStrength.trim() }),
        prescriptionRequired: entryRx,
      });
      await pharmacyApi.products.adopt({ catalogProductId: created.data.id, salePrice: entryPrice.trim() });
      setNote(`${created.data.name} created and adopted.`);
      setEntryName(''); setEntryGeneric(''); setEntryStrength(''); setEntryRx(false); setEntryPrice('');
      await invalidate();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Could not create the catalogue entry');
    } finally {
      setBusy(false);
    }
  }

  /**
   * One catalogue row: brand prominent, then generic/strength/form/manufacturer,
   * stock/status and price, actions unchanged. Arrows walk rows and skip the
   * group headings; the match label names anything that is not an exact
   * brand/barcode/SKU hit.
   */
  function renderRow(row: CatalogSearchItem): ReactNode {
    const key = rowKey(row);
    const expanded = expandedId === key;
    const index = rowIndex.get(row) ?? 0;
    const labelled = !(row.matchQuality === 'exact' && (row.matchedField === 'name' || row.matchedField === 'barcode' || row.matchedField === 'sku'));
    const identity = [row.genericName, row.strength, row.dosageForm, row.manufacturer]
      .filter((part): part is string => Boolean(part))
      .join(' · ');
    return (
      <li
        key={key}
        tabIndex={0}
        ref={(node) => {
          const slot = rowIndex.get(row);
          if (slot !== undefined) rowRefs.current[slot] = node;
        }}
        onKeyDown={(event) => onRowKeyDown(event, index)}
        style={{ border: `1px solid ${colors.border}`, borderRadius: 8, padding: spacing.sm }}
      >
        <div style={{ display: 'flex', justifyContent: 'space-between', gap: spacing.sm, flexWrap: 'wrap' }}>
          <div>
            <span>
              {highlightMedicineSpans(row.name, searchTerm).map((span, position) =>
                span.hit ? (
                  <mark key={position} style={{ background: 'transparent', color: colors.foreground, fontWeight: tokens.typography.weights.semibold, textDecoration: 'underline' }}>
                    {span.text}
                  </mark>
                ) : (
                  <span key={position}>{span.text}</span>
                ),
              )}
            </span>
            {identity !== '' && <span style={{ color: colors.muted }}> · {identity}</span>}
            <span style={{ ...STATUS_STYLE[row.shopStatus], marginLeft: spacing.sm, fontSize: tokens.typography.sizes.sm }}>
              {statusLabel(row.shopStatus)}
            </span>
            {labelled && (
              <span style={{ marginLeft: spacing.sm, fontSize: tokens.typography.sizes.sm, color: colors.warning }}>
                {describeMedicineMatch(row)}
              </span>
            )}
          </div>
          <span>{priceLabel(row)}{row.availableQuantity !== null && row.availableQuantity !== undefined ? ` · ${row.availableQuantity} in stock` : ''}</span>
        </div>
        <div style={{ display: 'flex', gap: spacing.xs, marginTop: spacing.xs, flexWrap: 'wrap' }}>
          {actionButtons(row)}
        </div>
        {expanded && panelAction === 'shelf' && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: spacing.xs, marginTop: spacing.sm }}>
            <input style={input} placeholder="SKU" value={sku} onChange={(event) => setSku(event.target.value)} />
            <input style={input} placeholder="Sale price, e.g. 10.00" value={price} onChange={(event) => setPrice(decimalEntry(event.target.value))} inputMode="decimal" />
            <div style={{ display: 'flex', gap: spacing.xs }}>
              <button type="button" style={button} disabled={busy} onClick={() => void (row.shopStatus === 'in_org' ? backOnShelf(row) : adopt(row))}>
                {row.shopStatus === 'in_org' ? 'Enable' : 'Adopt'}
              </button>
              <button type="button" style={quietButton} onClick={() => setExpandedId(null)}>Cancel</button>
            </div>
            {price.trim() === '' && (
              <p style={{ margin: 0, color: colors.muted, fontSize: tokens.typography.sizes.sm }}>
                A sale price is required before this row can sit on the shelf.
              </p>
            )}
          </div>
        )}
        {expanded && panelAction === 'po' && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: spacing.xs, marginTop: spacing.sm }}>
            <select style={input} value={selectedPoId} onChange={(event) => setSelectedPoId(event.target.value)}>
              <option value="">Start a new purchase order…</option>
              {draftOrders.map((order: PurchaseOrder) => (
                <option key={order.id} value={order.id}>
                  Draft {order.id.slice(0, 8)}{order.items.length > 0 ? ` · ${order.items.length} lines` : ''}
                </option>
              ))}
            </select>
            <input style={input} placeholder={`Quantity of ${row.name}`} value={quantity} onChange={(event) => setQuantity(decimalEntry(event.target.value))} inputMode="decimal" />
            <div style={{ display: 'flex', gap: spacing.xs }}>
              <button type="button" style={button} disabled={busy} onClick={() => void addToPurchaseOrder(row)}>Add to order</button>
              <button type="button" style={quietButton} onClick={() => setExpandedId(null)}>Cancel</button>
            </div>
          </div>
        )}
        {expanded && panelAction === 'alt' && (
          <AlternativesPanel
            row={row}
            mayAdopt={mayAdopt}
            onAct={(alternative, action) => expand(alternative, action)}
            onSell={() => router.push('/pos')}
            onClose={() => setExpandedId(null)}
          />
        )}
      </li>
    );
  }

  function priceLabel(row: CatalogSearchItem): string | null {
    if (row.salePrice !== null && row.salePrice !== undefined) return `৳${row.salePrice}`;
    const refs = [row.referenceUnitPrice, row.referenceStripPrice].filter(Boolean);
    return refs.length > 0 ? `৳${refs.join(' / ')}` : null;
  }

  function actionButtons(row: CatalogSearchItem): ReactNode {
    if (row.shopStatus === 'on_shelf') {
      return (
        <>
          <button type="button" style={quietButton} onClick={() => router.push('/pos')}>Sell</button>
          <button type="button" style={quietButton} onClick={() => router.push('/inventory')}>Add stock</button>
          {(row.genericName ?? '').trim() !== '' && (
            <button type="button" style={quietButton} onClick={() => expand(row, 'alt')}>Alternatives</button>
          )}
        </>
      );
    }
    return (
      <>
        {mayAdopt && (row.shopStatus === 'in_org' || row.kind === 'catalog') && (
          <button type="button" style={button} onClick={() => expand(row, 'shelf')}>
            {row.shopStatus === 'in_org' ? 'Add to shelf' : 'Adopt'}
          </button>
        )}
        <button type="button" style={quietButton} onClick={() => expand(row, 'po')}>Add to PO</button>
        {(row.genericName ?? '').trim() !== '' && (
          <button type="button" style={quietButton} onClick={() => expand(row, 'alt')}>Alternatives</button>
        )}
      </>
    );
  }

  return (
    <main className="split-grid split-grid--wide catalogue-page">
      <section className="surface catalogue-search" style={{ ...card, gridColumn: '1 / -1' }}>
        <span className="eyebrow">Shared medicine catalogue</span>
        <h1 style={{ margin: `${spacing.xs} 0 ${spacing.md}`, fontSize: tokens.typography.sizes.xl }}>Find a medicine</h1>
        <div style={{ display: 'flex', gap: spacing.xs, alignItems: 'center' }}>
          <input
            ref={searchRef}
            style={{ ...input, flex: 1, boxSizing: 'border-box' }}
            placeholder="Search by brand, generic name, strength, or barcode…"
            value={query}
            onChange={(event) => { resetFeedback(); setQuery(event.target.value); }}
            onKeyDown={(event) => {
              if (event.key === 'Escape') {
                setQuery('');
                event.currentTarget.focus();
              } else if (event.key === 'ArrowDown') {
                event.preventDefault();
                focusRow(0);
              }
            }}
            autoFocus
          />
          {query !== '' && (
            <button type="button" style={quietButton} aria-label="Clear search" onClick={() => setQuery('')}>✕</button>
          )}
        </div>
        <p role="status" aria-live="polite" style={{ margin: `${spacing.sm} 0 0`, color: colors.muted, fontSize: tokens.typography.sizes.sm }}>
          {searchQuery.isPending
            ? 'Searching…'
            : searched
              ? rows.length === 0
                ? ''
                : `${total} result${total === 1 ? '' : 's'}${rows.length < total ? ` — showing ${rows.length}` : ''}.`
              : ''}
        </p>
        {searched && !searchQuery.isPending && medicineMatchesAreFuzzy(rows) && (
          <p role="status" style={{ margin: `${spacing.xs} 0 0`, color: colors.warning, fontSize: tokens.typography.sizes.sm }}>
            No exact match—showing closest medicines.
          </p>
        )}
        {searched && !searchQuery.isPending && rows.length === 0 && (
          <p style={{ color: colors.muted, margin: `${spacing.xs} 0 0` }}>
            Nothing matched “{searchTerm}”.
            {mayAdopt ? ' Add it below to put it in the catalogue.' : ' Ask an owner or manager to add it.'}
          </p>
        )}
        {(error !== null || note !== null) && (
          <p role={error !== null ? 'alert' : undefined} style={{ margin: `${spacing.sm} 0 0`, color: error !== null ? colors.danger : colors.success }}>
            {error ?? note}
          </p>
        )}
      </section>

      <section className="surface catalogue-results" style={card}>
        <h2 style={{ marginTop: 0, fontSize: tokens.typography.sizes.lg }}>
          Results{rows.length > 0 ? ` (${rows.length})` : ''}
        </h2>
        {searchQuery.isPending && <p style={{ color: colors.muted }}>Searching…</p>}
        {!searched && <p style={{ color: colors.muted }}>Type above to search your shop and the shared medicine catalogue together.</p>}
        {groups.map((manufacturer) => (
          <div key={manufacturer.key} style={{ marginBottom: spacing.md }}>
            <h3 style={{ margin: `0 0 ${spacing.xs}`, fontSize: tokens.typography.sizes.sm, color: colors.muted, fontWeight: tokens.typography.weights.semibold }}>
              {manufacturer.label} ({manufacturer.count})
            </h3>
            {manufacturer.dosageGroups.map((dosage) => (
              <div key={dosage.key} style={{ marginBottom: spacing.sm }}>
                <h4 style={{ margin: `0 0 ${spacing.xs}`, fontSize: tokens.typography.sizes.sm, color: colors.muted, fontWeight: tokens.typography.weights.medium, paddingLeft: spacing.sm }}>
                  {dosage.label} ({dosage.items.length})
                </h4>
                <ul style={{ listStyle: 'none', margin: 0, paddingLeft: spacing.sm, display: 'flex', flexDirection: 'column', gap: spacing.sm }}>
                  {dosage.items.map((entry) => renderRow(entry.item))}
                </ul>
              </div>
            ))}
          </div>
        ))}
        {hasMoreResults(rows.length, total) && (
          <button type="button" style={{ ...quietButton, width: '100%' }} disabled={searchQuery.isFetching} onClick={() => setResultWindow((current) => nextResultWindow(current, total))}>
            {searchQuery.isFetching ? 'Loading…' : `Show more results (${total - rows.length} remaining)`}
          </button>
        )}
      </section>

      <section className="surface catalogue-create" style={{ ...card, display: 'flex', flexDirection: 'column', gap: spacing.md }}>
        <h2 style={{ margin: 0, fontSize: tokens.typography.sizes.lg }}>Add a missing medicine</h2>
        {mayAdopt ? (
          <p style={{ margin: 0, color: colors.muted }}>
            Creates the shared catalogue entry, then adopts it straight onto this shelf.
          </p>
        ) : (
          <p style={{ margin: 0, color: colors.muted }}>Owners and managers add catalogue entries.</p>
        )}
        <input style={input} placeholder="Brand name, e.g. Napa Extra" value={entryName} onChange={(event) => setEntryName(event.target.value)} disabled={!mayAdopt} />
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: spacing.xs }}>
          <input style={input} placeholder="Generic name" value={entryGeneric} onChange={(event) => setEntryGeneric(event.target.value)} disabled={!mayAdopt} />
          <input style={input} placeholder="Strength, e.g. 500mg" value={entryStrength} onChange={(event) => setEntryStrength(event.target.value)} disabled={!mayAdopt} />
          <input style={input} placeholder="Package unit" value={entryUnit} onChange={(event) => setEntryUnit(event.target.value)} disabled={!mayAdopt} />
          <label style={{ display: 'flex', alignItems: 'center', gap: spacing.xs }}>
            <input type="checkbox" checked={entryRx} onChange={(event) => setEntryRx(event.target.checked)} disabled={!mayAdopt} /> Prescription required
          </label>
          <input style={input} placeholder="Sale price, e.g. 10.00" value={entryPrice} onChange={(event) => setEntryPrice(decimalEntry(event.target.value))} inputMode="decimal" disabled={!mayAdopt} />
        </div>
        <button type="button" style={button} disabled={!mayAdopt || busy} onClick={() => void createEntryAndAdopt()}>Create and adopt</button>

        {mayManageShelf && (
          <>
            <h3 style={{ margin: `${spacing.md} 0 0` }}>Advanced</h3>
            <LegacyForms onDone={invalidate} onError={setError} />
          </>
        )}
      </section>
    </main>
  );
}

function statusLabel(status: CatalogSearchItem['shopStatus']): string {
  return status === 'on_shelf' ? 'on shelf' : status === 'in_org' ? 'in shop' : 'not stocked';
}

/** An alternative reuses the row actions, so it renders as the row the search would have returned. */
function alternativeAsSearchItem(alt: CatalogAlternativeItem): CatalogSearchItem {
  return {
    kind: 'catalog',
    catalogProductId: alt.catalogProductId,
    pharmacyProductId: alt.pharmacyProductId ?? null,
    storeProductId: alt.storeProductId ?? null,
    shopStatus: alt.shopStatus,
    name: alt.name,
    genericName: alt.genericName ?? null,
    strength: alt.strength ?? null,
    dosageFormId: alt.dosageFormId ?? null,
    dosageForm: alt.dosageForm ?? null,
    manufacturerId: alt.manufacturerId ?? null,
    manufacturer: alt.manufacturer ?? null,
    packageSize: alt.packageSize ?? null,
    packageUnit: alt.packageUnit ?? null,
    prescriptionRequired: alt.prescriptionRequired,
    referenceUnitPrice: alt.referenceUnitPrice ?? null,
    referenceStripPrice: alt.referenceStripPrice ?? null,
    salePrice: alt.salePrice ?? null,
    availableQuantity: alt.availableQuantity ?? null,
    sku: alt.sku ?? null,
    matchedField: 'genericName',
    matchQuality: 'exact',
    matchedText: alt.genericName ?? alt.name,
    matchScore: 1,
  };
}

/**
 * Other brands of one generic. A swap question, asked where the pharmacist is
 * already looking at the brand they were asked for: same-strength rows lead,
 * and every row carries the same shelf/adopt/order actions as a search result,
 * so "order the alternative instead" is one click from the answer.
 */
function AlternativesPanel({
  row,
  mayAdopt,
  onAct,
  onSell,
  onClose,
}: {
  row: CatalogSearchItem;
  mayAdopt: boolean;
  onAct: (alternative: CatalogSearchItem, action: 'shelf' | 'po') => void;
  onSell: () => void;
  onClose: () => void;
}): ReactNode {
  const generic = (row.genericName ?? '').trim();
  const alternativesQuery = useQuery({
    queryKey: ['catalogue', 'alternatives', row.catalogProductId ?? '-', generic],
    queryFn: async () =>
      await pharmacyApi.products.alternatives(
        {
          genericName: generic,
          ...(row.catalogProductId ? { excludeCatalogProductId: row.catalogProductId } : {}),
          ...(row.strength ? { strength: row.strength } : {}),
          ...(row.dosageFormId ? { dosageFormId: row.dosageFormId } : {}),
        },
        { limit: 25 },
      ),
    enabled: generic !== '',
    staleTime: 30_000,
  });
  const alternatives = alternativesQuery.data?.items ?? [];
  const total = alternativesQuery.data?.total ?? 0;

  function priceOf(alt: CatalogAlternativeItem): string | null {
    if (alt.salePrice !== null && alt.salePrice !== undefined) return `৳${alt.salePrice}`;
    const refs = [alt.referenceUnitPrice, alt.referenceStripPrice].filter(Boolean);
    return refs.length > 0 ? `৳${refs.join(' / ')}` : null;
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: spacing.xs, marginTop: spacing.sm }}>
      <strong>Other brands of {row.genericName}</strong>
      {alternativesQuery.isPending && <p style={{ margin: 0, color: colors.muted }}>Looking up other brands…</p>}
      {alternativesQuery.isError && <p style={{ margin: 0, color: colors.muted }}>Could not reach the shared catalogue.</p>}
      {!alternativesQuery.isPending && !alternativesQuery.isError && alternatives.length === 0 && (
        <p style={{ margin: 0, color: colors.muted }}>No other brands carry {row.genericName}.</p>
      )}
      {alternatives.map((alt) => {
        const identity = [alt.strength, alt.dosageForm, alt.manufacturer]
          .filter((part): part is string => Boolean(part))
          .join(' · ');
        return (
          <div key={alt.catalogProductId} style={{ border: `1px solid ${colors.border}`, borderRadius: 8, padding: spacing.xs, display: 'flex', flexDirection: 'column', gap: spacing.xs }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', gap: spacing.sm, flexWrap: 'wrap' }}>
              <span>
                {alt.name}
                {identity !== '' && <span style={{ color: colors.muted }}> · {identity}</span>}
                <span style={{ ...STATUS_STYLE[alt.shopStatus], marginLeft: spacing.sm, fontSize: tokens.typography.sizes.sm }}>
                  {statusLabel(alt.shopStatus)}
                </span>
                {!alt.sameStrength && (
                  <span style={{ marginLeft: spacing.sm, fontSize: tokens.typography.sizes.sm, color: colors.warning }}>
                    {alt.strength ? `different strength (${alt.strength})` : 'different strength'}
                  </span>
                )}
              </span>
              <span>{priceOf(alt)}{alt.availableQuantity !== null && alt.availableQuantity !== undefined ? ` · ${alt.availableQuantity} in stock` : ''}</span>
            </div>
            <div style={{ display: 'flex', gap: spacing.xs, flexWrap: 'wrap' }}>
              {alt.shopStatus === 'on_shelf' ? (
                <button type="button" style={quietButton} onClick={onSell}>Sell</button>
              ) : (
                <>
                  {mayAdopt && (
                    <button type="button" style={button} onClick={() => onAct(alternativeAsSearchItem(alt), 'shelf')}>
                      {alt.shopStatus === 'in_org' ? 'Add to shelf' : 'Adopt'}
                    </button>
                  )}
                  <button type="button" style={quietButton} onClick={() => onAct(alternativeAsSearchItem(alt), 'po')}>Add to PO</button>
                </>
              )}
            </div>
          </div>
        );
      })}
      {total > alternatives.length && (
        <p style={{ margin: 0, color: colors.muted, fontSize: tokens.typography.sizes.sm }}>…{total - alternatives.length} more</p>
      )}
      <button type="button" style={{ ...quietButton, alignSelf: 'flex-start' }} onClick={onClose}>Close</button>
    </div>
  );
}

/** The pre-adoption manual forms, kept for edge cases the search flow does not cover. */
function LegacyForms({ onDone, onError }: { onDone: () => Promise<void>; onError: (message: string | null) => void }): ReactNode {
  const [name, setName] = useState('');
  const [unit, setUnit] = useState('box');
  const [barcode, setBarcode] = useState('');
  const [selected, setSelected] = useState('');
  const [sku, setSku] = useState('');
  const [price, setPrice] = useState('');

  const productsQuery = useQuery({
    queryKey: ['catalogue', 'products'],
    queryFn: async () => (await pharmacyApi.products.listPharmacyProducts({ limit: 100 })).items,
    staleTime: 60_000,
  });
  const shelfQuery = useQuery({
    queryKey: ['catalogue', 'shelf'],
    queryFn: async () => (await pharmacyApi.products.listCurrentStoreProducts({ includeInactive: true })).items,
    staleTime: 30_000,
  });
  const products = productsQuery.data ?? [];

  const productFormValid = name.trim() !== '' && unit.trim() !== '';
  const shelfForm = shelfFormSchema.safeParse({ sku: sku.trim(), price: price.trim() });

  async function createProduct(): Promise<void> {
    if (!productFormValid) return;
    try {
      await pharmacyApi.products.createPharmacyProduct(
        barcode.trim() === ''
          ? { name: name.trim(), unit: unit.trim() }
          : { name: name.trim(), unit: unit.trim(), barcode: barcode.trim() },
      );
      setName('');
      setBarcode('');
      onError(null);
      await onDone();
    } catch (cause) {
      onError(cause instanceof Error ? cause.message : 'Could not create the product');
    }
  }

  async function enableOnShelf(): Promise<void> {
    if (!shelfForm.success || selected === '') return;
    try {
      await pharmacyApi.products.enableStoreProduct({ pharmacyProductId: selected, sku: sku.trim(), salePrice: price.trim() });
      setSku(''); setPrice(''); setSelected('');
      onError(null);
      await onDone();
    } catch (cause) {
      onError(cause instanceof Error ? cause.message : 'Could not enable the product');
    }
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: spacing.sm }}>
      <details>
        <summary>Custom product (no catalogue entry)</summary>
        <div style={{ display: 'flex', gap: spacing.xs, flexWrap: 'wrap', marginTop: spacing.xs }}>
          <input style={input} placeholder="Name" value={name} onChange={(event) => setName(event.target.value)} />
          <input style={{ ...input, width: 80 }} placeholder="Unit" value={unit} onChange={(event) => setUnit(event.target.value)} />
          <input style={input} placeholder="Barcode (optional)" value={barcode} onChange={(event) => setBarcode(event.target.value)} />
          <button type="button" style={button} onClick={() => void createProduct()}>Create</button>
        </div>
      </details>
      <details>
        <summary>Enable any product on this shelf</summary>
        <div style={{ display: 'flex', flexDirection: 'column', gap: spacing.xs, marginTop: spacing.xs }}>
          <select style={input} value={selected} onChange={(event) => setSelected(event.target.value)}>
            <option value="">Choose product…</option>
            {products.map((product) => <option key={product.id} value={product.id}>{product.name}</option>)}
          </select>
          <input style={input} placeholder="SKU" value={sku} onChange={(event) => setSku(event.target.value)} />
          <input style={input} placeholder="Sale price" value={price} onChange={(event) => setPrice(decimalEntry(event.target.value))} inputMode="decimal" />
          <button type="button" style={button} disabled={!shelfForm.success || selected === ''} onClick={() => void enableOnShelf()}>Enable</button>
        </div>
      </details>
      <p style={{ margin: 0, color: colors.muted, fontSize: tokens.typography.sizes.sm }}>
        Shelf holds {(shelfQuery.data ?? []).length} rows.
      </p>
    </div>
  );
}

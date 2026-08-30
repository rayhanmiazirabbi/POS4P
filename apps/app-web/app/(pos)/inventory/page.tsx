'use client';

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import type { InventoryIntake, ShelfItem } from '@pharmacy/api';
import { colors, spacing, tokens } from '@pharmacy/design-tokens';
import { toShelfProduct } from '@pharmacy/sync';
import Link from 'next/link';
import { useMemo, useState, type CSSProperties, type ReactNode } from 'react';

import { IntakeDrawer } from '@/components/intake-drawer';
import { MedicineFinder, type MedicineSelection } from '@/components/medicine-finder';
import { ProductDrawer } from '@/components/product-drawer';
import { pharmacyApi } from '@/lib/api';
import { useSession } from '@/lib/session';

type StockRow = { storeProductId: string; onHand: string; reserved: string; available: string; lowStock: boolean };
type ShelfEntry = { item: ShelfItem; onHand: string; available: string; lowStock: boolean };
type RackGroup = { key: string; label: string; unassigned: boolean; entries: ShelfEntry[] };

const surface: CSSProperties = { background: colors.surface, border: `1px solid ${colors.border}`, borderRadius: 12, padding: spacing.lg };
const rackUnit: CSSProperties = { background: colors.surface, border: `1px solid ${colors.border}`, borderRadius: 12, overflow: 'hidden' };
// The board is the physical shelf: one row of boxes resting on a thick line.
const shelfBoard: CSSProperties = { display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(140px, 1fr))', gap: spacing.sm, padding: `${spacing.xs} ${spacing.sm} ${spacing.sm}`, borderBottom: `3px solid ${colors.border}` };
const BOXES_PER_BOARD = 4;

export default function InventoryPage(): ReactNode {
  const { user } = useSession();
  const queryClient = useQueryClient();
  const [selection, setSelection] = useState<MedicineSelection | null>(null);
  const [lastIntake, setLastIntake] = useState<InventoryIntake | null>(null);
  const [panelEntry, setPanelEntry] = useState<ShelfEntry | null>(null);
  const [findQuery, setFindQuery] = useState('');
  const storeId = user?.storeId ?? null;

  const shelfQuery = useQuery({
    queryKey: ['inventory', 'shelf'],
    queryFn: async () => (await pharmacyApi.products.listCurrentStoreProducts()).items,
    staleTime: 20_000,
  });
  const stockQuery = useQuery({
    queryKey: ['inventory', 'stock', storeId],
    enabled: storeId !== null,
    queryFn: async () => (await pharmacyApi.inventory.stock(storeId as string)).data,
    staleTime: 15_000,
  });
  const shelf = shelfQuery.data ?? [];
  const stock = stockQuery.data ?? [];
  const stockById = useMemo(() => new Map(stock.map((row) => [row.storeProductId, row])), [stock]);
  const products = useMemo(
    () => shelf.map((row) => ({ ...toShelfProduct(row), availableQuantity: stockById.get(row.id)?.available ?? row.availableQuantity })),
    [shelf, stockById],
  );

  async function refresh(): Promise<void> {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ['inventory'] }),
      queryClient.invalidateQueries({ queryKey: ['pos', 'shelf'] }),
      queryClient.invalidateQueries({ queryKey: ['catalogue'] }),
    ]);
  }

  return (
    <main className="split-grid split-grid--wide inventory-page">
      <section className="surface inventory-intake" style={{ ...surface, display: 'grid', gap: spacing.md }}>
        <div>
          <span className="eyebrow">Inventory intake</span>
          <h1 style={{ margin: `${spacing.xs} 0`, fontSize: tokens.typography.sizes.xl }}>Scan, find, receive</h1>
          <p style={{ margin: 0, color: colors.muted }}>Existing shelf items and the global medicine catalogue share one receiving flow.</p>
        </div>
        <MedicineFinder products={products} actionLabel="Receive" autoFocus onSelect={setSelection} />
        <div style={{ display: 'flex', gap: spacing.sm, flexWrap: 'wrap' }}>
          <Link className="quiet-action" href="/inventory/stocktake" style={{ textDecoration: 'none' }}>Stocktake</Link>
          <Link className="quiet-action" href="/inventory/transfers" style={{ textDecoration: 'none' }}>Branch transfers</Link>
        </div>
        {(shelfQuery.isError || stockQuery.isError) && <p role="alert" className="form-error">Could not load the current inventory. Cached results may be incomplete.</p>}
      </section>

      <section className="surface inventory-stock" style={{ ...surface, display: 'grid', gap: spacing.md }}>
        <div>
          <span className="eyebrow">Current shelf</span>
          <h2 style={{ margin: `${spacing.xs} 0`, fontSize: tokens.typography.sizes.lg }}>{stock.length} stocked medicines</h2>
        </div>
        {lastIntake && (
          <div role="status" style={{ padding: spacing.md, background: '#e8f3ed', borderRadius: 8 }}>
            <strong>{lastIntake.name} received</strong>
            <div style={{ color: colors.muted, fontSize: tokens.typography.sizes.sm }}>On hand {lastIntake.balance.onHand} · available {lastIntake.balance.available} · batch {lastIntake.batch.batchNumber}</div>
          </div>
        )}
        {/* The reverse of the aisle walk: name in, rack out. The cashier's
            question is "where is it", and the rack map answers it visually --
            matching boxes light up, everything else steps back. */}
        <label style={{ fontSize: tokens.typography.sizes.sm, display: 'flex', flexDirection: 'column', gap: spacing.xs, maxWidth: 320 }}>
          Find on the rack
          <input
            className="field"
            placeholder="Medicine, SKU, or rack"
            value={findQuery}
            onChange={(event) => setFindQuery(event.target.value)}
          />
        </label>
        <RackShelf stock={stock} shelf={shelf} findQuery={findQuery.trim()} storeId={storeId} onOpen={setPanelEntry} />
      </section>

      {panelEntry && (
        <ProductDrawer
          item={panelEntry.item}
          onHand={panelEntry.onHand}
          available={panelEntry.available}
          onClose={() => setPanelEntry(null)}
          onReceive={() => { const item = panelEntry.item; setPanelEntry(null); setSelection({ kind: 'local', item: toShelfProduct(item) }); }}
        />
      )}
      {selection && (
        <IntakeDrawer
          selection={selection}
          source="supplier_receive"
          onClose={() => setSelection(null)}
          onSaved={(intake) => { setLastIntake(intake); setSelection(null); void refresh(); }}
        />
      )}
    </main>
  );
}

function normalizeRack(rack: string | null | undefined): string {
  return (rack ?? '').trim().replace(/\s+/g, ' ');
}

/**
 * Stock rows grouped by the rack label on their shelf item, mirroring the walk
 * down the aisle: one unit per rack, the medicines sitting on it underneath.
 * Labels are free text, so grouping is exact after trim and whitespace
 * collapse -- "Rack 1" and "rack1" stay separate racks until a rename merges
 * them. Stock rows with no matching shelf item are skipped.
 */
function groupByRack(stock: readonly StockRow[], shelf: readonly ShelfItem[]): RackGroup[] {
  const items = new Map(shelf.map((row) => [row.id, row]));
  const groups = new Map<string, RackGroup>();
  for (const row of stock) {
    const item = items.get(row.storeProductId);
    if (!item) continue;
    const label = normalizeRack(item.rack);
    const unassigned = label === '';
    const key = unassigned ? ' unassigned' : label.toLowerCase();
    const group = groups.get(key) ?? { key, label: unassigned ? 'Unassigned' : label, unassigned, entries: [] };
    group.entries.push({ item, onHand: row.onHand, available: row.available, lowStock: row.lowStock });
    groups.set(key, group);
  }
  return [...groups.values()].sort((a, b) =>
    a.unassigned === b.unassigned
      ? a.label.localeCompare(b.label, undefined, { numeric: true, sensitivity: 'base' })
      : a.unassigned ? 1 : -1,
  );
}

function chunk<T>(items: readonly T[], size: number): T[][] {
  const rows: T[][] = [];
  for (let index = 0; index < items.length; index += size) rows.push(items.slice(index, index + size));
  return rows;
}

function entryMatches(entry: ShelfEntry, needle: string): boolean {
  if (needle === '') return true;
  const haystack = `${entry.item.name} ${entry.item.sku ?? ''} ${normalizeRack(entry.item.rack)}`.toLowerCase();
  return haystack.includes(needle.toLowerCase());
}

/** Boards shown before a rack folds; the rest sit behind "Show all". */
const COLLAPSED_BOARDS = 3;

function RackShelf({
  stock,
  shelf,
  findQuery,
  storeId,
  onOpen,
}: {
  stock: readonly StockRow[];
  shelf: readonly ShelfItem[];
  findQuery: string;
  storeId: string | null;
  onOpen: (entry: ShelfEntry) => void;
}): ReactNode {
  const queryClient = useQueryClient();
  // Memoized because the groups rebuild on every page render otherwise --
  // drawer opens, refetches, intake saves -- and a shelf with thousands of
  // rows pays for a full regroup and sort each time.
  const groups = useMemo(() => groupByRack(stock, shelf), [stock, shelf]);
  const [expanded, setExpanded] = useState<Readonly<Record<string, boolean>>>({});
  const [renaming, setRenaming] = useState<string | null>(null);
  const [renameValue, setRenameValue] = useState('');
  const [renameError, setRenameError] = useState<string | null>(null);

  const rename = useMutation({
    mutationFn: async (input: { from: string; to: string }) => {
      if (storeId === null) throw new Error('Choose a branch first.');
      return await pharmacyApi.inventory.renameRack({ storeId, fromRack: input.from, toRack: input.to });
    },
    onSuccess: () => {
      setRenaming(null);
      setRenameValue('');
      setRenameError(null);
      void queryClient.invalidateQueries({ queryKey: ['inventory'] });
      void queryClient.invalidateQueries({ queryKey: ['pos', 'shelf'] });
    },
    onError: (cause) => setRenameError(cause instanceof Error ? cause.message : 'The rename was refused'),
  });

  if (stock.length === 0) return <p style={{ margin: 0, color: colors.muted }}>No inventory yet. Use the search to receive the first medicine.</p>;
  if (groups.length === 0) {
    // Stock arrived but no shelf item resolved for any of it: the boxes are the
    // only view now the table is gone, so say why the section looks empty.
    return <p style={{ margin: 0, color: colors.muted }}>Stock exists but no shelf details loaded for it. Refresh or check the alert above.</p>;
  }
  return (
    <div style={{ display: 'grid', gap: spacing.md }}>
      {groups.map((group) => {
        const boards = chunk(group.entries, BOXES_PER_BOARD);
        const matches = group.entries.filter((entry) => entryMatches(entry, findQuery));
        // A search overrides the fold: everything it found is shown, and a rack
        // with no hit collapses out of the way entirely.
        const searching = findQuery !== '';
        if (searching && matches.length === 0) return null;
        const visible = searching ? matches : group.entries;
        const shown = searching ? chunk(visible, BOXES_PER_BOARD) : (() => {
          const all = chunk(visible, BOXES_PER_BOARD);
          const collapsed = !expanded[group.key] && all.length > COLLAPSED_BOARDS;
          return collapsed ? all.slice(0, COLLAPSED_BOARDS) : all;
        })();
        return (
          <div key={group.key} style={{ ...rackUnit, borderStyle: group.unassigned ? 'dashed' : 'solid' }}>
            <header style={{ display: 'flex', justifyContent: 'space-between', gap: spacing.sm, padding: `${spacing.sm} ${spacing.md}`, background: colors.background, borderBottom: `1px solid ${colors.border}` }}>
              {renaming === group.key ? (
                <span style={{ display: 'flex', gap: spacing.xs, flex: 1 }}>
                  <input
                    className="field"
                    style={{ minHeight: 28 }}
                    value={renameValue}
                    autoFocus
                    onChange={(event) => setRenameValue(event.target.value)}
                    onKeyDown={(event) => {
                      if (event.key === 'Enter') rename.mutate({ from: group.label, to: renameValue });
                      if (event.key === 'Escape') setRenaming(null);
                    }}
                  />
                  <button type="button" className="quiet-action" style={{ minHeight: 28, padding: `${spacing.xs} ${spacing.sm}` }} disabled={rename.isPending} onClick={() => rename.mutate({ from: group.label, to: renameValue })}>Save</button>
                  <button type="button" className="quiet-action" style={{ minHeight: 28, padding: `${spacing.xs} ${spacing.sm}` }} onClick={() => setRenaming(null)}>Cancel</button>
                </span>
              ) : (
                <strong style={{ fontSize: tokens.typography.sizes.sm, color: group.unassigned ? colors.muted : colors.foreground }}>{group.label}</strong>
              )}
              <span style={{ display: 'flex', alignItems: 'center', gap: spacing.sm }}>
                <span style={{ color: colors.muted, fontSize: tokens.typography.sizes.sm }}>
                  {searching ? `${matches.length} of ${group.entries.length}` : `${group.entries.length} ${group.entries.length === 1 ? 'item' : 'items'}`}
                </span>
                {!group.unassigned && renaming !== group.key && (
                  <button
                    type="button"
                    className="quiet-action"
                    style={{ minHeight: 26, padding: `${spacing.xs} ${spacing.sm}`, fontSize: tokens.typography.sizes.sm }}
                    onClick={() => { setRenaming(group.key); setRenameValue(group.label); setRenameError(null); }}
                  >
                    Rename
                  </button>
                )}
              </span>
            </header>
            <div style={{ display: 'grid', gap: spacing.md, padding: spacing.sm }}>
              {shown.map((row, index) => (
                <div key={index} style={shelfBoard}>
                  {row.map((entry) => <MedicineBox key={entry.item.id} entry={entry} onOpen={onOpen} />)}
                </div>
              ))}
              {!searching && (() => {
                const all = chunk(group.entries, BOXES_PER_BOARD);
                const collapsed = !expanded[group.key] && all.length > COLLAPSED_BOARDS;
                if (collapsed) {
                  return (
                    <button
                      type="button"
                      className="quiet-action"
                      style={{ minHeight: 30, fontSize: tokens.typography.sizes.sm }}
                      aria-expanded={false}
                      onClick={() => setExpanded((current) => ({ ...current, [group.key]: true }))}
                    >
                      Show all {group.entries.length} on {group.label}
                    </button>
                  );
                }
                if (all.length > COLLAPSED_BOARDS) {
                  return (
                    <button
                      type="button"
                      className="quiet-action"
                      style={{ minHeight: 30, fontSize: tokens.typography.sizes.sm }}
                      aria-expanded
                      onClick={() => setExpanded((current) => ({ ...current, [group.key]: false }))}
                    >
                      Show first {COLLAPSED_BOARDS * BOXES_PER_BOARD}
                    </button>
                  );
                }
                return null;
              })()}
            </div>
          </div>
        );
      })}
      {renameError && <p role="alert" className="form-error" style={{ margin: 0 }}>{renameError}</p>}
    </div>
  );
}

function MedicineBox({ entry, onOpen }: { entry: ShelfEntry; onOpen: (entry: ShelfEntry) => void }): ReactNode {
  const { item, available, lowStock } = entry;
  const out = available === '0';
  const meta = [item.strength, item.dosageForm].filter((part): part is string => Boolean(part)).join(' · ');
  return (
    <button
      type="button"
      className="shelf-box"
      onClick={() => onOpen(entry)}
      title={`${item.name}: batches, history, corrections`}
      aria-label={`${item.name}${meta ? `, ${meta}` : ''}, ${out ? 'out of stock' : `${available} available`}. Open shelf record`}
      style={{
        ...(lowStock && !out ? { borderColor: colors.warning, background: 'rgba(161, 98, 7, 0.08)' } : {}),
        borderStyle: out ? 'dashed' : 'solid',
      }}
    >
      <strong style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', fontSize: tokens.typography.sizes.sm, color: out ? colors.muted : colors.foreground }}>{item.name}</strong>
      {meta !== '' && <small style={{ color: colors.muted, fontSize: tokens.typography.sizes.sm }}>{meta}</small>}
      <span style={{ color: out ? colors.danger : lowStock ? colors.warning : colors.foreground, fontWeight: tokens.typography.weights.semibold, fontSize: tokens.typography.sizes.sm }}>
        {out ? 'Out' : `${available}${item.unit ? ` ${item.unit}` : ''}`}
      </span>
    </button>
  );
}

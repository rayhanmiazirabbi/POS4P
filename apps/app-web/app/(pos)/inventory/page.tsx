'use client';

import { useQuery, useQueryClient } from '@tanstack/react-query';
import type { InventoryIntake, ShelfItem } from '@pharmacy/api';
import { colors, spacing, tokens } from '@pharmacy/design-tokens';
import { toShelfProduct } from '@pharmacy/sync';
import { useMemo, useState, type CSSProperties, type ReactNode } from 'react';

import { IntakeDrawer } from '@/components/intake-drawer';
import { MedicineFinder, type MedicineSelection } from '@/components/medicine-finder';
import { pharmacyApi } from '@/lib/api';
import { useSession } from '@/lib/session';

type StockRow = { storeProductId: string; onHand: string; reserved: string; available: string; lowStock: boolean };
const surface: CSSProperties = { background: colors.surface, border: `1px solid ${colors.border}`, borderRadius: 12, padding: spacing.lg };

export default function InventoryPage(): ReactNode {
  const { user } = useSession();
  const queryClient = useQueryClient();
  const [selection, setSelection] = useState<MedicineSelection | null>(null);
  const [lastIntake, setLastIntake] = useState<InventoryIntake | null>(null);
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
        <StockTable stock={stock} shelf={shelf} onReceive={(item) => setSelection({ kind: 'local', item: toShelfProduct(item) })} />
      </section>

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

function StockTable({ stock, shelf, onReceive }: { stock: readonly StockRow[]; shelf: readonly ShelfItem[]; onReceive: (item: ShelfItem) => void }): ReactNode {
  const names = new Map(shelf.map((row) => [row.id, row]));
  if (stock.length === 0) return <p style={{ margin: 0, color: colors.muted }}>No inventory yet. Use the search to receive the first medicine.</p>;
  return (
    <div style={{ overflowX: 'auto' }}>
      <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: tokens.typography.sizes.sm }}>
        <thead><tr style={{ color: colors.muted, textAlign: 'left' }}><th>Medicine</th><th>On hand</th><th>Available</th><th /></tr></thead>
        <tbody>{stock.map((row) => {
          const item = names.get(row.storeProductId);
          return <tr key={row.storeProductId} style={{ borderTop: `1px solid ${colors.border}` }}>
            <td style={{ padding: `${spacing.sm} 0` }}><strong>{item?.name ?? row.storeProductId}</strong><br /><span style={{ color: colors.muted }}>{item?.sku ?? ''}{item?.rack ? ` · ${item.rack}` : ''}</span></td>
            <td>{row.onHand}</td><td style={{ color: row.lowStock ? colors.warning : colors.foreground }}>{row.available}</td>
            <td>{item && <button type="button" className="quiet-action" onClick={() => onReceive(item)}>Receive</button>}</td>
          </tr>;
        })}</tbody>
      </table>
    </div>
  );
}

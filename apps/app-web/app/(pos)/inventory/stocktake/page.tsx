'use client';

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import type { ShelfItem } from '@pharmacy/api';
import { colors, spacing, tokens } from '@pharmacy/design-tokens';
import { toShelfProduct } from '@pharmacy/sync';
import { useMemo, useState, type CSSProperties, type ReactNode } from 'react';

import { MedicineFinder, type MedicineSelection } from '@/components/medicine-finder';
import { pharmacyApi } from '@/lib/api';
import { decimalEntry } from '@/lib/numeric-input';
import { useSession } from '@/lib/session';

const surface: CSSProperties = { background: colors.surface, border: `1px solid ${colors.border}`, borderRadius: 12, padding: spacing.lg };

/**
 * A physical count session: pick products, type what is really on the shelf,
 * finalize to book every variance as a correction.
 *
 * The count is deliberately blind to system quantities while lines are entered --
 * the counter reads the shelf, not the screen -- and the variance is shown only
 * on finalize, where it becomes a signed correction instead of an argument.
 */
export default function StocktakePage(): ReactNode {
  const { user } = useSession();
  const queryClient = useQueryClient();
  const storeId = user?.storeId ?? null;
  const [activeId, setActiveId] = useState<string | null>(null);
  const [countQuantity, setCountQuantity] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [summary, setSummary] = useState<string | null>(null);

  const shelfQuery = useQuery({
    queryKey: ['inventory', 'shelf'],
    queryFn: async () => (await pharmacyApi.products.listCurrentStoreProducts()).items,
    staleTime: 20_000,
  });
  const stocktakesQuery = useQuery({
    queryKey: ['inventory', 'stocktakes'],
    queryFn: async () => (await pharmacyApi.inventory.listStocktakes({ limit: 25 })).items,
    staleTime: 10_000,
  });
  const activeQuery = useQuery({
    queryKey: ['inventory', 'stocktake', activeId],
    enabled: activeId !== null,
    queryFn: async () => (await pharmacyApi.inventory.readStocktake(activeId as string)).data,
    staleTime: 5_000,
  });

  const shelf = shelfQuery.data ?? [];
  const products = useMemo(() => shelf.map((row) => toShelfProduct(row)), [shelf]);
  const stocktakes = stocktakesQuery.data ?? [];
  const active = activeQuery.data ?? null;
  const draftLines = active?.status === 'draft' ? active.lines : [];

  async function refresh(): Promise<void> {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ['inventory', 'stocktakes'] }),
      queryClient.invalidateQueries({ queryKey: ['inventory', 'stocktake', activeId] }),
      queryClient.invalidateQueries({ queryKey: ['inventory'] }),
    ]);
  }

  const open = useMutation({
    mutationFn: async (note: string) => (await pharmacyApi.inventory.createStocktake({ note })).data,
    onSuccess: (created) => { setActiveId(created.id); setError(null); setSummary(null); void refresh(); },
    onError: (cause) => setError(cause instanceof Error ? cause.message : 'The count session could not be opened'),
  });

  const addLine = useMutation({
    mutationFn: async (input: { storeProductId: string; countedQuantity: string }) =>
      await pharmacyApi.inventory.addStocktakeLine(activeId as string, input),
    onSuccess: (_result, input) => {
      const counted = draftLines.find((line) => line.storeProductId === input.storeProductId);
      setSummary(counted ? `Recounted: ${input.countedQuantity} replaces ${counted.countedQuantity}` : `Counted ${input.countedQuantity}`);
      setError(null);
      setCountQuantity('');
      void refresh();
    },
    onError: (cause) => { setSummary(null); setError(cause instanceof Error ? cause.message : 'The line was refused'); },
  });

  const finalize = useMutation({
    mutationFn: async () => (await pharmacyApi.inventory.finalizeStocktake(activeId as string)).data,
    onSuccess: (result) => {
      setError(null);
      setSummary(`Finalized: ${result.correctedLines} corrected, ${result.unchangedLines} matched. Variances are booked as adjustments.`);
      void refresh();
    },
    onError: (cause) => { setSummary(null); setError(cause instanceof Error ? cause.message : 'Finalize was refused'); },
  });

  function selectForCount(selection: MedicineSelection): void {
    if (activeId === null || active?.status !== 'draft') {
      setError('Open a count session first.');
      return;
    }
    if (selection.kind !== 'local') {
      setError("Count this branch's own shelf items; catalogue entries are not on this shelf.");
      return;
    }
    const counted = countQuantity.trim();
    if (counted === '' || Number(counted) < 0) {
      setError('Enter the counted quantity first (0 or more).');
      return;
    }
    addLine.mutate({ storeProductId: selection.item.id, countedQuantity: counted });
  }

  return (
    <main className="page-shell split-grid split-grid--wide">
      <section className="surface" style={{ ...surface, display: 'grid', gap: spacing.md }}>
        <div>
          <span className="eyebrow">Stocktake</span>
          <h1 style={{ margin: `${spacing.xs} 0`, fontSize: tokens.typography.sizes.xl }}>Count the shelf</h1>
          <p style={{ margin: 0, color: colors.muted }}>
            Search a medicine, type what the shelf actually holds, repeat. Finalizing books every difference as a signed correction.
          </p>
        </div>

        <div style={{ display: 'flex', gap: spacing.sm, alignItems: 'center', flexWrap: 'wrap' }}>
          <button
            type="button"
            className="primary-action"
            disabled={open.isPending || active?.status === 'draft'}
            onClick={() => open.mutate(`Count ${new Date().toLocaleDateString()}`)}
          >
            {active?.status === 'draft' ? 'Count in progress' : 'Start a count'}
          </button>
          {draftLines.length > 0 && (
            <button type="button" className="quiet-action" disabled={finalize.isPending} onClick={() => finalize.mutate()}>
              {finalize.isPending ? 'Booking…' : `Finalize (${draftLines.length} lines)`}
            </button>
          )}
        </div>

        {active?.status === 'draft' && (
          <div style={{ display: 'grid', gap: spacing.sm }}>
            <label style={{ fontSize: tokens.typography.sizes.sm, display: 'flex', flexDirection: 'column', gap: spacing.xs, maxWidth: 220 }}>
              Counted quantity
              <input
                className="field"
                inputMode="decimal"
                placeholder="What the shelf holds"
                value={countQuantity}
                onChange={(event) => setCountQuantity(decimalEntry(event.target.value))}
              />
            </label>
            <MedicineFinder products={products} actionLabel="Count" onSelect={selectForCount} />
          </div>
        )}

        {error && <p role="alert" className="form-error" style={{ margin: 0 }}>{error}</p>}
        {summary && <p role="status" style={{ margin: 0, color: colors.success }}>{summary}</p>}
      </section>

      <section className="surface" style={{ ...surface, display: 'grid', gap: spacing.md }}>
        <div>
          <span className="eyebrow">Session</span>
          <h2 style={{ margin: `${spacing.xs} 0`, fontSize: tokens.typography.sizes.lg }}>
            {active ? `${active.status === 'draft' ? 'Counting' : 'Completed'} · ${active.note ?? 'no note'}` : 'No session selected'}
          </h2>
        </div>

        {activeId === null && <p style={{ margin: 0, color: colors.muted }}>Start a count, or pick a past session below.</p>}

        {draftLines.length > 0 && (
          <div style={{ overflowX: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: tokens.typography.sizes.sm }}>
              <thead><tr style={{ color: colors.muted, textAlign: 'left' }}><th>Medicine</th><th>Counted</th></tr></thead>
              <tbody>{draftLines.map((line) => (
                <tr key={line.storeProductId} style={{ borderTop: `1px solid ${colors.border}` }}>
                  <td style={{ padding: `${spacing.sm} 0` }}>{line.productName}<br /><span style={{ color: colors.muted }}>{line.sku}</span></td>
                  <td>{line.countedQuantity}</td>
                </tr>
              ))}</tbody>
            </table>
            <p style={{ margin: 0, color: colors.muted, fontSize: tokens.typography.sizes.sm }}>
              Variances are revealed at finalize, after the count is done.
            </p>
          </div>
        )}

        {active?.status === 'completed' && active.lines.length > 0 && (
          <div style={{ overflowX: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: tokens.typography.sizes.sm }}>
              <thead><tr style={{ color: colors.muted, textAlign: 'left' }}><th>Medicine</th><th>Counted</th><th>System</th><th>Variance</th></tr></thead>
              <tbody>{active.lines.map((line) => (
                <tr key={line.storeProductId} style={{ borderTop: `1px solid ${colors.border}` }}>
                  <td style={{ padding: `${spacing.sm} 0` }}>{line.productName}<br /><span style={{ color: colors.muted }}>{line.sku}</span></td>
                  <td>{line.countedQuantity}</td>
                  <td>{line.systemQuantity}</td>
                  <td style={{ color: Number(line.variance) === 0 ? colors.muted : Number(line.variance) < 0 ? colors.danger : colors.success }}>
                    {Number(line.variance) > 0 ? '+' : ''}{Number(line.variance)}
                  </td>
                </tr>
              ))}</tbody>
            </table>
          </div>
        )}

        <div style={{ display: 'grid', gap: spacing.xs }}>
          <h3 style={{ margin: 0, fontSize: tokens.typography.sizes.md }}>Past sessions</h3>
          {stocktakes.filter((row) => row.id !== activeId).map((row) => (
            <button
              key={row.id}
              type="button"
              className="quiet-action"
              style={{ justifyContent: 'space-between', textAlign: 'left' }}
              onClick={() => { setActiveId(row.id); setSummary(null); setError(null); }}
            >
              <span>{new Date(row.createdAt).toLocaleString()} · {row.status === 'draft' ? 'counting' : 'completed'}</span>
              <span style={{ color: colors.muted }}>{row.lines.length} lines</span>
            </button>
          ))}
          {stocktakes.filter((row) => row.id !== activeId).length === 0 && (
            <p style={{ margin: 0, color: colors.muted, fontSize: tokens.typography.sizes.sm }}>No other sessions yet.</p>
          )}
        </div>
      </section>
    </main>
  );
}

'use client';

import { useQuery, useQueryClient } from '@tanstack/react-query';
import type { ShelfItem } from '@pharmacy/api';
import { colors, spacing, tokens } from '@pharmacy/design-tokens';
import { useMemo, useState, type CSSProperties, type ReactNode } from 'react';
import { z } from 'zod';

import { pharmacyApi } from '@/lib/api';
import { useSession } from '@/lib/session';
import { decimalAmount, fieldIssue, positiveQuantity } from '@/lib/validation';

type StockRow = { storeProductId: string; onHand: string; reserved: string; available: string; lowStock: boolean };

const card: CSSProperties = { background: colors.surface, border: `1px solid ${colors.border}`, borderRadius: 12, padding: spacing.lg };
const input: CSSProperties = { padding: spacing.sm, borderRadius: 8, border: `1px solid ${colors.border}` };
const button: CSSProperties = { ...input, cursor: 'pointer', background: colors.primary, color: colors.primaryForeground, border: 'none', fontWeight: tokens.typography.weights.medium };

/**
 * A shelf row as a person recognises it.
 *
 * `GET /products/current` now returns the product name alongside the shelf row, so
 * these lists no longer have to identify a medicine by SKU alone. Receiving a batch
 * against the wrong line is a stock correction and a wasted delivery, and `PARA-500`
 * versus `PARA-650` in a dropdown is exactly the confusion that causes it.
 */
function shelfLabel(row: ShelfItem): string {
  return `${row.name} · ${row.sku}`;
}

/** A delivery is counted in whole units and costed in money; both are checked
 *  here because the server's refusal arrives after the delivery van has left. */
const receiveBatchSchema = z.object({
  storeProductId: z.string().min(1),
  batchNumber: z.string().trim().min(1),
  unitCost: decimalAmount,
  quantity: positiveQuantity,
});

export default function InventoryPage(): ReactNode {
  const { user } = useSession();
  const queryClient = useQueryClient();
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [form, setForm] = useState({ storeProductId: '', batchNumber: '', expiryDate: '', unitCost: '', quantity: '' });

  const storeId = user?.storeId ?? null;

  const stockQuery = useQuery({
    queryKey: ['inventory', 'stock', storeId],
    enabled: storeId !== null,
    queryFn: async () => (await pharmacyApi.inventory.stock({ query: { storeId: storeId as string } })).data,
    staleTime: 15_000,
  });
  const shelfQuery = useQuery({
    queryKey: ['inventory', 'shelf'],
    queryFn: async () => (await pharmacyApi.products.listCurrentStoreProducts()).items,
    staleTime: 30_000,
  });

  const stock = stockQuery.data ?? [];
  const shelf = shelfQuery.data ?? [];
  const labelById = useMemo(() => new Map(shelf.map((row) => [row.id, shelfLabel(row)])), [shelf]);

  const parsedForm = receiveBatchSchema.safeParse(form);
  // An empty field is an unfinished form -- the disabled button covers it. A typed
  // value that cannot be a cost or a count is worth naming while it is on screen.
  const unitCostProblem = form.unitCost.trim() === '' ? null : fieldIssue(decimalAmount.safeParse(form.unitCost));
  const quantityProblem = form.quantity.trim() === '' ? null : fieldIssue(positiveQuantity.safeParse(form.quantity));

  async function receive(): Promise<void> {
    setError(null);
    setNote(null);
    if (!parsedForm.success) return;
    try {
      await pharmacyApi.inventory.receiveBatch(
        {
          storeProductId: form.storeProductId,
          batchNumber: form.batchNumber.trim(),
          ...(form.expiryDate.trim() === '' ? {} : { expiryDate: form.expiryDate }),
          unitCost: form.unitCost.trim(),
          quantity: form.quantity.trim(),
        },
        { idempotencyKey: `receive-${crypto.randomUUID()}` },
      );
      setNote('Batch received.');
      setForm({ storeProductId: '', batchNumber: '', expiryDate: '', unitCost: '', quantity: '' });
      await queryClient.invalidateQueries({ queryKey: ['inventory'] });
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Could not receive the batch');
    }
  }

  return (
    <main className="split-grid split-grid--wide">
      <section style={card}>
        <h2 style={{ marginTop: 0, fontSize: tokens.typography.sizes.lg }}>Stock ({stock.length})</h2>
        {stockQuery.isError && (
          <p role="alert" style={{ margin: 0, color: colors.danger }}>{stockQuery.error instanceof Error ? stockQuery.error.message : 'Could not load stock'}</p>
        )}
        {stockQuery.isPending && <p style={{ color: colors.muted }}>Loading…</p>}
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: tokens.typography.sizes.sm }}>
          <thead>
            <tr style={{ textAlign: 'left', color: colors.muted }}>
              <th>Product</th><th>On hand</th><th>Reserved</th><th>Available</th>
            </tr>
          </thead>
          <tbody>
            {stock.map((row) => (
              <tr key={row.storeProductId} style={{ color: row.lowStock ? colors.warning : colors.foreground }}>
                <td style={{ padding: `${spacing.xs} 0` }}>{labelById.get(row.storeProductId) ?? row.storeProductId.slice(0, 8)}</td>
                <td>{row.onHand}</td>
                <td>{row.reserved}</td>
                <td>{row.available}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {!stockQuery.isPending && stock.length === 0 && <p style={{ color: colors.muted }}>No stock rows yet.</p>}
      </section>

      <section style={{ ...card, display: 'flex', flexDirection: 'column', gap: spacing.sm }}>
        <h2 style={{ margin: 0, fontSize: tokens.typography.sizes.lg }}>Receive batch</h2>
        <select style={input} value={form.storeProductId} onChange={(event) => setForm({ ...form, storeProductId: event.target.value })}>
          <option value="">Choose shelf product…</option>
          {shelf.map((row) => <option key={row.id} value={row.id}>{shelfLabel(row)}</option>)}
        </select>
        <input style={input} placeholder="Batch number" value={form.batchNumber} onChange={(event) => setForm({ ...form, batchNumber: event.target.value })} />
        <input style={input} type="date" value={form.expiryDate} onChange={(event) => setForm({ ...form, expiryDate: event.target.value })} />
        <div>
          <input style={{ ...input, width: '100%', boxSizing: 'border-box' }} placeholder="Unit cost, e.g. 5.00" value={form.unitCost} onChange={(event) => setForm({ ...form, unitCost: event.target.value })} inputMode="decimal" />
          {unitCostProblem !== null && (
            <p role="alert" style={{ margin: `${spacing.xs} 0 0`, color: colors.danger, fontSize: tokens.typography.sizes.sm }}>{unitCostProblem}</p>
          )}
        </div>
        <div>
          <input style={{ ...input, width: '100%', boxSizing: 'border-box' }} placeholder="Quantity" value={form.quantity} onChange={(event) => setForm({ ...form, quantity: event.target.value })} inputMode="decimal" />
          {quantityProblem !== null && (
            <p role="alert" style={{ margin: `${spacing.xs} 0 0`, color: colors.danger, fontSize: tokens.typography.sizes.sm }}>{quantityProblem}</p>
          )}
        </div>
        <button type="button" style={button} disabled={!parsedForm.success} onClick={() => void receive()}>
          Receive
        </button>
        {(error !== null || note !== null || shelfQuery.isError) && (
          <p role={error !== null ? 'alert' : undefined} style={{ margin: 0, color: error !== null ? colors.danger : colors.success }}>
            {error ??
              note ??
              (shelfQuery.isError
                ? shelfQuery.error instanceof Error
                  ? shelfQuery.error.message
                  : 'Could not load stock'
                : '')}
          </p>
        )}
      </section>
    </main>
  );
}

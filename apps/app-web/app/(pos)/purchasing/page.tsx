'use client';

import { useQuery, useQueryClient } from '@tanstack/react-query';
import type { Purchase, ShelfItem, Supplier } from '@pharmacy/api';
import { colors, spacing, tokens } from '@pharmacy/design-tokens';
import { useState, type CSSProperties, type ReactNode } from 'react';
import { z } from 'zod';

import { pharmacyApi } from '@/lib/api';
import { decimalAmount, fieldIssue, positiveQuantity } from '@/lib/validation';

const card: CSSProperties = { background: colors.surface, border: `1px solid ${colors.border}`, borderRadius: 12, padding: spacing.lg };
const input: CSSProperties = { padding: spacing.sm, borderRadius: 8, border: `1px solid ${colors.border}` };
const button: CSSProperties = { ...input, cursor: 'pointer', background: colors.primary, color: colors.primaryForeground, border: 'none', fontWeight: tokens.typography.weights.medium };

type DraftLine = { storeProductId: string; quantity: string; unitCost: string; batchNumber: string; expiryDate: string };

const emptyLine: DraftLine = { storeProductId: '', quantity: '', unitCost: '', batchNumber: '', expiryDate: '' };

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

/** A draft needs its supplier and at least one complete line: what to buy, how
 *  much, and at what cost. Confirmed purchases book batches, so a line missing
 *  any of those would only fail later, further from the person who typed it. */
const draftLineSchema = z.object({
  storeProductId: z.string().min(1),
  quantity: positiveQuantity,
  unitCost: decimalAmount,
  batchNumber: z.string().trim().min(1),
});

export default function PurchasingPage(): ReactNode {
  const queryClient = useQueryClient();
  const [supplierId, setSupplierId] = useState('');
  const [invoiceNumber, setInvoiceNumber] = useState('');
  const [lines, setLines] = useState<readonly DraftLine[]>([{ ...emptyLine }]);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);

  const purchasesQuery = useQuery({
    queryKey: ['purchasing', 'purchases'],
    queryFn: async () => (await pharmacyApi.purchases.list({ limit: 25 })).items,
    staleTime: 15_000,
  });
  const suppliersQuery = useQuery({
    queryKey: ['purchasing', 'suppliers'],
    queryFn: async () => (await pharmacyApi.suppliers.list({ limit: 100 })).items,
    staleTime: 60_000,
  });
  const shelfQuery = useQuery({
    queryKey: ['purchasing', 'shelf'],
    queryFn: async () => (await pharmacyApi.products.listCurrentStoreProducts()).items,
    staleTime: 30_000,
  });

  const purchases = purchasesQuery.data ?? [];
  const suppliers = suppliersQuery.data ?? [];
  const shelf = shelfQuery.data ?? [];

  // A line the operator has not started typing is not an error; one with a
  // quantity or cost in it is checked field by field while it is on screen.
  function lineProblems(line: DraftLine): { quantity: string | null; unitCost: string | null } {
    return {
      quantity: line.quantity.trim() === '' ? null : fieldIssue(positiveQuantity.safeParse(line.quantity)),
      unitCost: line.unitCost.trim() === '' ? null : fieldIssue(decimalAmount.safeParse(line.unitCost)),
    };
  }

  // The payload keeps the page's old rule -- incomplete lines are dropped -- but
  // the button now demands the supplier plus at least one line that survives it.
  const completeLines = lines.filter((line) => line.storeProductId !== '' && line.quantity !== '' && line.unitCost !== '' && line.batchNumber !== '');
  const validLines = completeLines.filter((line) => draftLineSchema.safeParse(line).success);
  const draftReady = supplierId !== '' && completeLines.length > 0 && validLines.length === completeLines.length;

  async function refreshLists(): Promise<void> {
    await queryClient.invalidateQueries({ queryKey: ['purchasing'] });
  }

  async function createDraft(): Promise<void> {
    setError(null);
    setNote(null);
    if (!draftReady) return;
    try {
      const response = await pharmacyApi.purchases.create({
        supplierId,
        ...(invoiceNumber.trim() === '' ? {} : { invoiceNumber: invoiceNumber.trim() }),
        items: validLines.map((line) => ({
          storeProductId: line.storeProductId,
          quantity: line.quantity,
          unitCost: line.unitCost,
          batchNumber: line.batchNumber,
          ...(line.expiryDate === '' ? {} : { expiryDate: line.expiryDate }),
        })),
      });
      setNote(`Draft ${response.data.id.slice(0, 8)} created.`);
      setLines([{ ...emptyLine }]);
      setInvoiceNumber('');
      await refreshLists();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Could not create the draft');
    }
  }

  async function confirm(purchaseId: string): Promise<void> {
    setError(null);
    setNote(null);
    try {
      await pharmacyApi.purchases.confirm(purchaseId);
      setNote('Purchase confirmed; batches booked.');
      await refreshLists();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Could not confirm the purchase');
    }
  }

  return (
    <main className="split-grid split-grid--wide">
      <section style={card}>
        <h2 style={{ marginTop: 0, fontSize: tokens.typography.sizes.lg }}>Purchases ({purchases.length})</h2>
        {purchasesQuery.isError && (
          <p role="alert" style={{ margin: 0, color: colors.danger }}>{purchasesQuery.error instanceof Error ? purchasesQuery.error.message : 'Could not load purchases'}</p>
        )}
        {purchasesQuery.isPending && <p style={{ color: colors.muted }}>Loading…</p>}
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: tokens.typography.sizes.sm }}>
          <thead>
            <tr style={{ textAlign: 'left', color: colors.muted }}>
              <th>Date</th><th>Invoice</th><th>Status</th><th>Total</th><th />
            </tr>
          </thead>
          <tbody>
            {purchases.map((purchase) => (
              <tr key={purchase.id}>
                <td style={{ padding: `${spacing.xs} 0` }}>{purchase.purchasedAt}</td>
                <td>{purchase.invoiceNumber ?? '—'}</td>
                <td style={{ color: purchase.status === 'confirmed' ? colors.success : colors.warning }}>{purchase.status}</td>
                <td>{purchase.totalAmount ? `৳${purchase.totalAmount}` : '—'}</td>
                <td>
                  {purchase.status === 'draft' && (
                    <button type="button" style={{ ...input, cursor: 'pointer' }} onClick={() => void confirm(purchase.id)}>Confirm</button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {!purchasesQuery.isPending && purchases.length === 0 && <p style={{ color: colors.muted }}>No purchases yet.</p>}
      </section>

      <section style={{ ...card, display: 'flex', flexDirection: 'column', gap: spacing.sm }}>
        <h2 style={{ margin: 0, fontSize: tokens.typography.sizes.lg }}>New purchase draft</h2>
        <select style={input} value={supplierId} onChange={(event) => setSupplierId(event.target.value)}>
          <option value="">Choose supplier…</option>
          {suppliers.map((supplier) => <option key={supplier.id} value={supplier.id}>{supplier.name}</option>)}
        </select>
        <input style={input} placeholder="Invoice number (optional)" value={invoiceNumber} onChange={(event) => setInvoiceNumber(event.target.value)} />
        {lines.map((line, index) => {
          const problems = lineProblems(line);
          return (
            <div key={index} style={{ display: 'flex', flexDirection: 'column', gap: spacing.xs, border: `1px solid ${colors.border}`, borderRadius: 8, padding: spacing.sm }}>
              <select style={input} value={line.storeProductId} onChange={(event) => setLines(lines.map((entry, i) => (i === index ? { ...entry, storeProductId: event.target.value } : entry)))}>
                <option value="">Shelf product…</option>
                {shelf.map((row) => <option key={row.id} value={row.id}>{shelfLabel(row)}</option>)}
              </select>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: spacing.xs }}>
                <div>
                  <input style={{ ...input, width: '100%', boxSizing: 'border-box' }} placeholder="Qty" value={line.quantity} onChange={(event) => setLines(lines.map((entry, i) => (i === index ? { ...entry, quantity: event.target.value } : entry)))} inputMode="decimal" />
                  {problems.quantity !== null && <p role="alert" style={{ margin: `${spacing.xs} 0 0`, color: colors.danger, fontSize: tokens.typography.sizes.sm }}>{problems.quantity}</p>}
                </div>
                <div>
                  <input style={{ ...input, width: '100%', boxSizing: 'border-box' }} placeholder="Unit cost" value={line.unitCost} onChange={(event) => setLines(lines.map((entry, i) => (i === index ? { ...entry, unitCost: event.target.value } : entry)))} inputMode="decimal" />
                  {problems.unitCost !== null && <p role="alert" style={{ margin: `${spacing.xs} 0 0`, color: colors.danger, fontSize: tokens.typography.sizes.sm }}>{problems.unitCost}</p>}
                </div>
                <input style={input} placeholder="Batch no." value={line.batchNumber} onChange={(event) => setLines(lines.map((entry, i) => (i === index ? { ...entry, batchNumber: event.target.value } : entry)))} />
                <input style={input} type="date" value={line.expiryDate} onChange={(event) => setLines(lines.map((entry, i) => (i === index ? { ...entry, expiryDate: event.target.value } : entry)))} />
              </div>
            </div>
          );
        })}
        <div style={{ display: 'flex', gap: spacing.sm }}>
          <button type="button" style={{ ...button, background: colors.surface, color: colors.foreground, border: `1px solid ${colors.border}` }} onClick={() => setLines([...lines, { ...emptyLine }])}>Add line</button>
          <button type="button" style={button} disabled={!draftReady} onClick={() => void createDraft()}>Create draft</button>
        </div>
        {(error !== null || note !== null || suppliersQuery.isError || shelfQuery.isError) && (
          <p role={error !== null ? 'alert' : undefined} style={{ margin: 0, color: error !== null ? colors.danger : colors.success }}>
            {error ??
              note ??
              (suppliersQuery.isError
                ? suppliersQuery.error instanceof Error
                  ? suppliersQuery.error.message
                  : 'Could not load suppliers'
                : 'Could not load suppliers')}
          </p>
        )}
      </section>
    </main>
  );
}

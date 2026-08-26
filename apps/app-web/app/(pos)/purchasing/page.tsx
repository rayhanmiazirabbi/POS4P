'use client';

import { useQuery, useQueryClient } from '@tanstack/react-query';
import type { Purchase, PurchaseOrder, PurchaseOrderStatusWire, ShelfItem, Supplier } from '@pharmacy/api';
import { colors, spacing, tokens } from '@pharmacy/design-tokens';
import { can } from '@pharmacy/permissions';
import { useState, type CSSProperties, type ReactNode } from 'react';
import { z } from 'zod';

import { pharmacyApi } from '@/lib/api';
import { useSession } from '@/lib/session';
import { decimalAmount, fieldIssue, positiveQuantity } from '@/lib/validation';

const card: CSSProperties = { background: colors.surface, border: `1px solid ${colors.border}`, borderRadius: 12, padding: spacing.lg };
const input: CSSProperties = { padding: spacing.sm, borderRadius: 8, border: `1px solid ${colors.border}` };
const button: CSSProperties = { ...input, cursor: 'pointer', background: colors.primary, color: colors.primaryForeground, border: 'none', fontWeight: tokens.typography.weights.medium };
const quietButton: CSSProperties = { ...input, cursor: 'pointer', background: colors.surface };

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

const PO_STATUSES: readonly PurchaseOrderStatusWire[] = ['draft', 'ordered', 'closed', 'cancelled'];

function orderLabel(order: PurchaseOrder): string {
  return `Draft ${order.id.slice(0, 8)} · ${order.items.length} line${order.items.length === 1 ? '' : 's'}`;
}

export default function PurchasingPage(): ReactNode {
  const { user } = useSession();
  const role = user?.role ?? null;
  const mayManagePurchases = role !== null && can(role, 'purchases.manage');

  return (
    <main className="split-grid split-grid--wide">
      <PurchaseOrdersSection mayManagePurchases={mayManagePurchases} />
      {mayManagePurchases ? <PurchasesSection /> : null}
    </main>
  );
}

function PurchaseOrdersSection({ mayManagePurchases }: { mayManagePurchases: boolean }): ReactNode {
  const queryClient = useQueryClient();
  const [statusFilter, setStatusFilter] = useState<PurchaseOrderStatusWire | ''>('');
  const [selectedId, setSelectedId] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [skippedLines, setSkippedLines] = useState<readonly string[]>([]);
  const [busy, setBusy] = useState(false);

  // New-order dialog state.
  const [supplierId, setSupplierId] = useState('');
  const [expectedAt, setExpectedAt] = useState('');
  const [orderNote, setOrderNote] = useState('');

  // Detail editor state.
  const detail = useQuery({
    queryKey: ['purchasing', 'purchase-order', selectedId],
    queryFn: async () => (await pharmacyApi.purchaseOrders.read(selectedId)).data,
    enabled: selectedId !== '',
  });
  const [newLineName, setNewLineName] = useState('');
  const [newLineQty, setNewLineQty] = useState('10');
  const [newLineCost, setNewLineCost] = useState('');
  const [convertSupplierId, setConvertSupplierId] = useState('');

  const ordersQuery = useQuery({
    queryKey: ['purchasing', 'purchase-orders', statusFilter],
    queryFn: async () =>
      (
        await pharmacyApi.purchaseOrders.list(
          statusFilter === '' ? {} : { status: statusFilter },
          { limit: 50 },
        )
      ).items,
    staleTime: 15_000,
  });
  const suppliersQuery = useQuery({
    queryKey: ['purchasing', 'suppliers'],
    queryFn: async () => (await pharmacyApi.suppliers.list({ limit: 100 })).items,
    staleTime: 60_000,
  });
  const suppliers = suppliersQuery.data ?? [];

  function resetFeedback(): void {
    setError(null);
    setNote(null);
    setSkippedLines([]);
  }

  async function invalidate(): Promise<void> {
    await queryClient.invalidateQueries({ queryKey: ['purchasing', 'purchase-orders'] });
  }

  async function run(action: () => Promise<void>): Promise<void> {
    resetFeedback();
    setBusy(true);
    try {
      await action();
      await invalidate();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'The request failed');
    } finally {
      setBusy(false);
    }
  }

  async function createOrder(): Promise<void> {
    await run(async () => {
      const created = await pharmacyApi.purchaseOrders.create({
        ...(supplierId === '' ? {} : { supplierId }),
        ...(expectedAt === '' ? {} : { expectedAt }),
        ...(orderNote.trim() === '' ? {} : { note: orderNote.trim() }),
      });
      setNote(`Order ${created.data.id.slice(0, 8)} created as a draft.`);
      setSupplierId(''); setExpectedAt(''); setOrderNote('');
      setSelectedId(created.data.id);
    });
  }

  async function addLine(orderId: string): Promise<void> {
    if (newLineName.trim() === '') {
      setError('A line needs a name.');
      return;
    }
    await run(async () => {
      await pharmacyApi.purchaseOrders.addItem(orderId, {
        name: newLineName.trim(),
        quantity: newLineQty,
        ...(newLineCost.trim() === '' ? {} : { estUnitCost: newLineCost.trim() }),
      });
      setNewLineName(''); setNewLineQty('10'); setNewLineCost('');
      setNote('Line added.');
    });
  }

  async function convert(order: PurchaseOrder): Promise<void> {
    await run(async () => {
      const result = (
        await pharmacyApi.purchaseOrders.toPurchase(
          order.id,
          convertSupplierId === '' ? {} : { supplierId: convertSupplierId },
        )
      ).data;
      setSkippedLines(result.skipped.map((line) => `${line.name}: ${line.reason}`));
      setNote(`Purchase draft ${result.purchaseId.slice(0, 8)} created (${result.convertedCount} lines).`);
    });
  }

  const selected = selectedId === '' ? null : detail.data ?? null;

  return (
    <section style={{ ...card, display: 'flex', flexDirection: 'column', gap: spacing.md }}>
      <h2 style={{ marginTop: 0, fontSize: tokens.typography.sizes.lg }}>Purchase Orders</h2>

      {(error !== null || note !== null || ordersQuery.isError) && (
        <p role={error !== null ? 'alert' : undefined} style={{ margin: 0, color: error !== null ? colors.danger : colors.success }}>
          {error ??
            note ??
            (ordersQuery.isError ? (ordersQuery.error instanceof Error ? ordersQuery.error.message : 'Could not load orders') : '')}
        </p>
      )}
      {skippedLines.length > 0 && (
        <div style={{ border: `1px solid ${colors.border}`, borderRadius: 8, padding: spacing.sm }}>
          <strong>Lines not converted:</strong>
          <ul style={{ margin: `${spacing.xs} 0 0`, paddingLeft: spacing.lg }}>
            {skippedLines.map((line) => <li key={line}>{line}</li>)}
          </ul>
        </div>
      )}

      <div style={{ display: 'flex', gap: spacing.xs, flexWrap: 'wrap' }}>
        <select
          style={input}
          value={statusFilter}
          onChange={(event) => setStatusFilter(event.target.value as PurchaseOrderStatusWire | '')}
        >
          <option value="">All statuses</option>
          {PO_STATUSES.map((status) => <option key={status} value={status}>{status}</option>)}
        </select>
      </div>

      <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: tokens.typography.sizes.sm }}>
        <thead>
          <tr style={{ textAlign: 'left', color: colors.muted }}>
            <th>Created</th><th>Status</th><th>Note</th><th />
          </tr>
        </thead>
        <tbody>
          {(ordersQuery.data ?? []).map((order) => (
            <tr key={order.id}>
              <td style={{ padding: `${spacing.xs} 0` }}>{order.createdAt.slice(0, 10)}</td>
              <td style={{ color: order.status === 'draft' ? colors.warning : order.status === 'cancelled' ? colors.danger : colors.success }}>{order.status}</td>
              <td>{order.note ?? '—'}</td>
              <td><button type="button" style={quietButton} onClick={() => { resetFeedback(); setSelectedId(order.id); }}>Open</button></td>
            </tr>
          ))}
        </tbody>
      </table>
      {ordersQuery.data !== undefined && ordersQuery.data.length === 0 && <p style={{ margin: 0, color: colors.muted }}>No purchase orders yet.</p>}

      <h3 style={{ margin: 0 }}>Start an order</h3>
      <select style={input} value={supplierId} onChange={(event) => setSupplierId(event.target.value)}>
        <option value="">Supplier (optional for now)</option>
        {suppliers.map((supplier: Supplier) => <option key={supplier.id} value={supplier.id}>{supplier.name}</option>)}
      </select>
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: spacing.xs }}>
        <input style={input} type="date" value={expectedAt} onChange={(event) => setExpectedAt(event.target.value)} />
        <input style={input} placeholder="Note" value={orderNote} onChange={(event) => setOrderNote(event.target.value)} />
      </div>
      <button type="button" style={button} disabled={busy} onClick={() => void createOrder()}>Create draft order</button>

      {selected !== null && (
        <div style={{ borderTop: `1px solid ${colors.border}`, paddingTop: spacing.md, display: 'flex', flexDirection: 'column', gap: spacing.sm }}>
          <h3 style={{ margin: 0 }}>{orderLabel(selected)} — {selected.status}</h3>
          <ul style={{ listStyle: 'none', margin: 0, padding: 0, display: 'flex', flexDirection: 'column', gap: spacing.xs }}>
            {selected.items.map((item) => (
              <li key={item.id} style={{ display: 'flex', justifyContent: 'space-between', gap: spacing.xs, flexWrap: 'wrap' }}>
                <span>{item.name} · qty {item.quantity}{item.estUnitCost ? ` · ~৳${item.estUnitCost}` : ''}</span>
                {selected.status === 'draft' && (
                  <button type="button" style={quietButton} onClick={() => void run(async () => {
                    await pharmacyApi.purchaseOrders.removeItem(selected.id, item.id);
                    setNote('Line removed.');
                  })}>Remove</button>
                )}
              </li>
            ))}
            {selected.items.length === 0 && <li style={{ color: colors.muted }}>No lines yet.</li>}
          </ul>
          {selected.status === 'draft' && (
            <>
              <div style={{ display: 'grid', gridTemplateColumns: '2fr 1fr 1fr', gap: spacing.xs }}>
                <input style={input} placeholder="What to order (free text)" value={newLineName} onChange={(event) => setNewLineName(event.target.value)} />
                <input style={input} placeholder="Qty" value={newLineQty} onChange={(event) => setNewLineQty(event.target.value)} inputMode="decimal" />
                <input style={input} placeholder="Est. cost" value={newLineCost} onChange={(event) => setNewLineCost(event.target.value)} inputMode="decimal" />
              </div>
              <button type="button" style={quietButton} disabled={busy} onClick={() => void addLine(selected.id)}>Add line</button>
            </>
          )}
          <div style={{ display: 'flex', gap: spacing.xs, flexWrap: 'wrap' }}>
            {selected.status === 'draft' && (
              <button type="button" style={button} onClick={() => void run(async () => {
                await pharmacyApi.purchaseOrders.order(selected.id);
                setNote('Marked as ordered.');
              })}>Mark ordered</button>
            )}
            {selected.status === 'ordered' && (
              <>
                <button type="button" style={button} onClick={() => void run(async () => {
                  await pharmacyApi.purchaseOrders.close(selected.id);
                  setNote('Order closed.');
                })}>Close</button>
                {mayManagePurchases && (
                  <>
                    <select style={input} value={convertSupplierId} onChange={(event) => setConvertSupplierId(event.target.value)}>
                      <option value="">Convert with supplier…</option>
                      {suppliers.map((supplier: Supplier) => <option key={supplier.id} value={supplier.id}>{supplier.name}</option>)}
                    </select>
                    <button type="button" style={button} disabled={busy} onClick={() => void convert(selected)}>Convert to purchase draft</button>
                  </>
                )}
              </>
            )}
            {(selected.status === 'draft' || selected.status === 'ordered') && (
              <button type="button" style={quietButton} onClick={() => void run(async () => {
                await pharmacyApi.purchaseOrders.cancel(selected.id);
                setNote('Order cancelled.');
              })}>Cancel order</button>
            )}
            <button type="button" style={quietButton} onClick={() => setSelectedId('')}>Close panel</button>
          </div>
          {mayManagePurchases && selected.status === 'ordered' && (
            <p style={{ margin: 0, color: colors.muted, fontSize: tokens.typography.sizes.sm }}>
              Conversion creates a purchase draft; confirm it on the purchases side to book stock and supplier due.
            </p>
          )}
        </div>
      )}
    </section>
  );
}

function PurchasesSection(): ReactNode {
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

  function lineProblems(line: DraftLine): { quantity: string | null; unitCost: string | null } {
    return {
      quantity: line.quantity.trim() === '' ? null : fieldIssue(positiveQuantity.safeParse(line.quantity)),
      unitCost: line.unitCost.trim() === '' ? null : fieldIssue(decimalAmount.safeParse(line.unitCost)),
    };
  }

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

      <h3 style={{ marginBottom: spacing.xs }}>New purchase draft</h3>
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
  );
}

'use client';

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import type { ShelfItem, StockTransferSummary } from '@pharmacy/api';
import { colors, spacing, tokens } from '@pharmacy/design-tokens';
import { toShelfProduct } from '@pharmacy/sync';
import { useMemo, useState, type CSSProperties, type ReactNode } from 'react';

import { MedicineFinder, type MedicineSelection } from '@/components/medicine-finder';
import { pharmacyApi } from '@/lib/api';
import { decimalEntry } from '@/lib/numeric-input';
import { useSession } from '@/lib/session';

const surface: CSSProperties = { background: colors.surface, border: `1px solid ${colors.border}`, borderRadius: 12, padding: spacing.lg };

type DraftLine = { storeProductId: string; name: string; sku: string; quantity: string };

const STATUS_LABELS: Readonly<Record<string, string>> = {
  draft: 'Draft',
  in_transit: 'In transit',
  received: 'Received',
  cancelled: 'Cancelled',
};

/**
 * Branch-to-branch stock moves: open a draft, ship it (FEFO pulls the batches),
 * receive it at the other branch.
 *
 * A transfer is two stock events, not one: shipping writes TRANSFER movements out
 * of the source, receiving writes fresh batches into the destination. The buttons
 * follow that order, and each side only sees its own step -- the receiving branch
 * signs in with its own store to accept.
 */
export default function TransfersPage(): ReactNode {
  const { user } = useSession();
  const queryClient = useQueryClient();
  const storeId = user?.storeId ?? null;
  const [lines, setLines] = useState<DraftLine[]>([]);
  const [quantity, setQuantity] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const shelfQuery = useQuery({
    queryKey: ['inventory', 'shelf'],
    queryFn: async () => (await pharmacyApi.products.listCurrentStoreProducts()).items,
    staleTime: 20_000,
  });
  const storesQuery = useQuery({
    queryKey: ['stores', 'all'],
    queryFn: async () => (await pharmacyApi.stores.list({ limit: 100 })).items,
    staleTime: 60_000,
  });
  const transfersQuery = useQuery({
    queryKey: ['inventory', 'transfers'],
    queryFn: async () => (await pharmacyApi.inventory.listTransfers({}, { limit: 25 })).items,
    staleTime: 10_000,
  });

  const shelf = shelfQuery.data ?? [];
  const products = useMemo(() => shelf.map((row) => toShelfProduct(row)), [shelf]);
  const stores = storesQuery.data ?? [];
  const otherStores = stores.filter((store) => store.id !== storeId);
  const [toStoreId, setToStoreId] = useState('');
  const transfers = transfersQuery.data ?? [];

  async function refresh(): Promise<void> {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ['inventory', 'transfers'] }),
      queryClient.invalidateQueries({ queryKey: ['inventory'] }),
      queryClient.invalidateQueries({ queryKey: ['pos', 'shelf'] }),
    ]);
  }

  function pickLine(selection: MedicineSelection): void {
    if (selection.kind !== 'local') {
      setError('Only this branch\'s shelf items can move.');
      return;
    }
    const trimmed = quantity.trim();
    if (trimmed === '' || Number(trimmed) <= 0) {
      setError('Enter the quantity to move first.');
      return;
    }
    setError(null);
    setLines((current) => [
      ...current.filter((line) => line.storeProductId !== selection.item.id),
      { storeProductId: selection.item.id, name: selection.item.name, sku: selection.item.sku, quantity: trimmed },
    ]);
    setQuantity('');
  }

  const create = useMutation({
    mutationFn: async () => {
      if (storeId === null || toStoreId === '') throw new Error('Choose the receiving branch.');
      if (lines.length === 0) throw new Error('Add at least one line.');
      const number = `TR-${Date.now().toString(36).toUpperCase()}`;
      return (await pharmacyApi.inventory.createTransfer({
        transferNumber: number,
        fromStoreId: storeId,
        toStoreId,
        items: lines.map((line) => ({ storeProductId: line.storeProductId, quantity: line.quantity })),
      })).data;
    },
    onSuccess: (created) => {
      setLines([]);
      setNotice(`Draft ${created.transferNumber} opened. Ship it when the stock leaves this branch.`);
      setError(null);
      void refresh();
    },
    onError: (cause) => { setNotice(null); setError(cause instanceof Error ? cause.message : 'The transfer was refused'); },
  });

  const act = useMutation({
    mutationFn: async (input: { transfer: StockTransferSummary; step: 'ship' | 'receive' | 'cancel' }) => {
      if (input.step === 'ship') return await pharmacyApi.inventory.shipTransfer(input.transfer.id);
      if (input.step === 'receive') return await pharmacyApi.inventory.receiveTransfer(input.transfer.id);
      return await pharmacyApi.inventory.cancelTransfer(input.transfer.id);
    },
    onSuccess: (_result, input) => {
      setNotice(
        input.step === 'ship'
          ? 'Shipped. The receiving branch signs in and accepts it on this screen.'
          : input.step === 'receive'
            ? 'Received. The batches are on the destination shelf now.'
            : 'Draft cancelled.',
      );
      setError(null);
      void refresh();
    },
    onError: (cause) => { setNotice(null); setError(cause instanceof Error ? cause.message : 'The action was refused'); },
  });

  const storeName = (id: string): string => stores.find((store) => store.id === id)?.name ?? id.slice(0, 8);

  return (
    <main className="page-shell split-grid split-grid--wide">
      <section className="surface" style={{ ...surface, display: 'grid', gap: spacing.md }}>
        <div>
          <span className="eyebrow">Branch transfer</span>
          <h1 style={{ margin: `${spacing.xs} 0`, fontSize: tokens.typography.sizes.xl }}>Move stock between branches</h1>
          <p style={{ margin: 0, color: colors.muted }}>
            Shipping pulls batches FEFO from this branch; receiving books them fresh at the other end.
          </p>
        </div>

        <label style={{ fontSize: tokens.typography.sizes.sm, display: 'flex', flexDirection: 'column', gap: spacing.xs }}>
          Receiving branch
          <select className="field" value={toStoreId} onChange={(event) => setToStoreId(event.target.value)}>
            <option value="">Choose a branch…</option>
            {otherStores.map((store) => <option key={store.id} value={store.id}>{store.name}</option>)}
          </select>
        </label>

        <label style={{ fontSize: tokens.typography.sizes.sm, display: 'flex', flexDirection: 'column', gap: spacing.xs, maxWidth: 220 }}>
          Quantity
          <input
            className="field"
            inputMode="decimal"
            placeholder="Units to move"
            value={quantity}
            onChange={(event) => setQuantity(decimalEntry(event.target.value))}
          />
        </label>

        <MedicineFinder products={products} actionLabel="Add line" onSelect={pickLine} />

        {lines.length > 0 && (
          <div style={{ display: 'grid', gap: spacing.xs }}>
            {lines.map((line) => (
              <div key={line.storeProductId} style={{ display: 'flex', alignItems: 'baseline', gap: spacing.sm, fontSize: tokens.typography.sizes.sm }}>
                <span style={{ flex: 1 }}>{line.name} <span style={{ color: colors.muted }}>· {line.sku}</span></span>
                <span>×{line.quantity}</span>
                <button
                  type="button"
                  className="quiet-action"
                  style={{ minHeight: 28, padding: `${spacing.xs} ${spacing.sm}` }}
                  onClick={() => setLines((current) => current.filter((entry) => entry.storeProductId !== line.storeProductId))}
                >
                  Remove
                </button>
              </div>
            ))}
            <button type="button" className="primary-action" disabled={create.isPending} onClick={() => create.mutate()}>
              {create.isPending ? 'Opening…' : 'Open draft transfer'}
            </button>
          </div>
        )}

        {error && <p role="alert" className="form-error" style={{ margin: 0 }}>{error}</p>}
        {notice && <p role="status" style={{ margin: 0, color: colors.success }}>{notice}</p>}
      </section>

      <section className="surface" style={{ ...surface, display: 'grid', gap: spacing.md }}>
        <div>
          <span className="eyebrow">Transfers</span>
          <h2 style={{ margin: `${spacing.xs} 0`, fontSize: tokens.typography.sizes.lg }}>This branch, newest first</h2>
        </div>
        {transfersQuery.isPending && <p style={{ margin: 0, color: colors.muted }}>Loading…</p>}
        {transfers.length === 0 && !transfersQuery.isPending && <p style={{ margin: 0, color: colors.muted }}>No transfers touch this branch yet.</p>}
        {transfers.map((transfer) => (
          <div key={transfer.id} style={{ display: 'flex', alignItems: 'center', gap: spacing.sm, borderBottom: `1px solid ${colors.border}`, paddingBottom: spacing.sm, fontSize: tokens.typography.sizes.sm }}>
            <span style={{ flex: 1 }}>
              <strong>{transfer.transferNumber}</strong>
              <br />
              <span style={{ color: colors.muted }}>
                {storeName(transfer.fromStoreId)} → {storeName(transfer.toStoreId)}
                {transfer.shippedAt ? ` · shipped ${new Date(transfer.shippedAt).toLocaleDateString()}` : transfer.receivedAt ? ` · received ${new Date(transfer.receivedAt).toLocaleDateString()}` : ''}
              </span>
            </span>
            <span style={{ color: transfer.status === 'received' ? colors.success : transfer.status === 'cancelled' ? colors.muted : transfer.status === 'in_transit' ? colors.warning : colors.foreground }}>
              {STATUS_LABELS[transfer.status] ?? transfer.status}
            </span>
            {transfer.status === 'draft' && (
              <>
                {transfer.fromStoreId === storeId && (
                  <button type="button" className="quiet-action" style={{ minHeight: 28, padding: `${spacing.xs} ${spacing.sm}` }} disabled={act.isPending} onClick={() => act.mutate({ transfer, step: 'ship' })}>Ship</button>
                )}
                <button type="button" className="quiet-action danger-action" style={{ minHeight: 28, padding: `${spacing.xs} ${spacing.sm}` }} disabled={act.isPending} onClick={() => act.mutate({ transfer, step: 'cancel' })}>Cancel</button>
              </>
            )}
            {transfer.status === 'in_transit' && transfer.toStoreId === storeId && (
              <button type="button" className="primary-action" style={{ minHeight: 28, padding: `${spacing.xs} ${spacing.sm}` }} disabled={act.isPending} onClick={() => act.mutate({ transfer, step: 'receive' })}>Receive</button>
            )}
          </div>
        ))}
      </section>
    </main>
  );
}

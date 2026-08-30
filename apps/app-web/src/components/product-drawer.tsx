'use client';

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import type { ProductBatch, ShelfItem } from '@pharmacy/api';
import { colors, spacing, tokens } from '@pharmacy/design-tokens';
import { useState, type CSSProperties, type ReactNode } from 'react';

import { pharmacyApi } from '@/lib/api';
import { decimalEntry } from '@/lib/numeric-input';

const label: CSSProperties = { display: 'flex', flexDirection: 'column', gap: spacing.xs, fontSize: tokens.typography.sizes.sm };
const field: CSSProperties = { padding: `${spacing.sm} ${spacing.sm}`, borderRadius: 8, border: `1px solid ${colors.border}` };
const quiet: CSSProperties = { ...field, cursor: 'pointer', background: colors.surface };

const MOVEMENT_LABELS: Readonly<Record<string, string>> = {
  receipt: 'Received',
  sale: 'Sold',
  return: 'Returned',
  adjustment: 'Adjusted',
  damage: 'Damaged',
  transfer: 'Transfer',
};

/**
 * The back-of-shelf panel for one product: its batches, its ledger, and the
 * corrections a manager is allowed to make.
 *
 * Every shelf action that is not receiving lives here, because "why is this
 * number wrong" and "fix this number" are the same conversation -- splitting
 * them across screens means the person fixing never sees the history that
 * explains what they are fixing.
 */
export function ProductDrawer({
  item,
  onHand,
  available,
  onClose,
  onReceive,
}: {
  item: ShelfItem;
  onHand: string;
  available: string;
  onClose: () => void;
  onReceive: () => void;
}): ReactNode {
  const queryClient = useQueryClient();
  const [quantity, setQuantity] = useState('');
  const [reason, setReason] = useState('');
  const [damage, setDamage] = useState(false);
  const [batchId, setBatchId] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const batchesQuery = useQuery({
    queryKey: ['inventory', 'batches', item.id],
    queryFn: async () => (await pharmacyApi.inventory.batches(item.id)).data,
    staleTime: 15_000,
  });
  const movementsQuery = useQuery({
    queryKey: ['inventory', 'movements', item.id],
    queryFn: async () => (await pharmacyApi.inventory.movements({ storeProductId: item.id }, { limit: 15 })).items,
    staleTime: 10_000,
  });

  async function refresh(): Promise<void> {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ['inventory'] }),
      queryClient.invalidateQueries({ queryKey: ['pos', 'shelf'] }),
      queryClient.invalidateQueries({ queryKey: ['reports'] }),
    ]);
  }

  const adjust = useMutation({
    mutationFn: async (body: { quantity: string; reason: string; damage: boolean; batchId?: string }) =>
      await pharmacyApi.inventory.adjust({ storeProductId: item.id, ...body }),
    onSuccess: async (result) => {
      setQuantity('');
      setReason('');
      setDamage(false);
      setBatchId('');
      setError(null);
      setNotice(`Balance now ${result.data.onHand} on hand · ${result.data.available} available`);
      await refresh();
    },
    onError: (cause) => {
      setNotice(null);
      setError(cause instanceof Error ? cause.message : 'The correction was refused');
    },
  });

  const batches = batchesQuery.data ?? [];
  const movements = movementsQuery.data ?? [];
  const meta = [item.strength, item.dosageForm].filter((part): part is string => Boolean(part)).join(' · ');

  function submitAdjustment(): void {
    const trimmed = reason.trim();
    if (quantity.trim() === '' || trimmed.length < 3) {
      setError('Enter a signed quantity and a reason (at least 3 characters).');
      return;
    }
    if (Number(quantity) === 0) {
      setError('The quantity must be non-zero: negative removes, positive adds.');
      return;
    }
    adjust.mutate({ quantity: quantity.trim(), reason: trimmed, damage, ...(batchId ? { batchId } : {}) });
  }

  function disposeBatch(batch: ProductBatch): void {
    adjust.mutate({
      quantity: `-${batch.available}`,
      reason: `Expired batch ${batch.batchNumber} disposed`,
      damage: true,
      batchId: batch.batchId,
    });
  }

  return (
    <div className="drawer-backdrop" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
      <div
        className="intake-drawer"
        role="dialog"
        aria-modal="true"
        aria-labelledby="product-drawer-title"
        onKeyDown={(event) => { if (event.key === 'Escape') { event.preventDefault(); onClose(); } }}
      >
        <header>
          <div>
            <span className="eyebrow">Shelf record</span>
            <h2 id="product-drawer-title" style={{ margin: `${spacing.xs} 0` }}>{item.name}</h2>
            <p style={{ margin: 0, color: colors.muted, fontSize: tokens.typography.sizes.sm }}>
              {item.sku}{item.rack ? ` · ${item.rack}` : ''}{meta ? ` · ${meta}` : ''} · on hand {onHand} · available {available}
            </p>
          </div>
          <button type="button" className="quiet-action" onClick={onClose}>Close</button>
        </header>

        <section style={{ display: 'grid', gap: spacing.sm }}>
          <h3 style={{ margin: 0, fontSize: tokens.typography.sizes.md }}>Batches</h3>
          {batchesQuery.isPending && <p style={{ margin: 0, color: colors.muted }}>Loading batches…</p>}
          {batchesQuery.isError && <p role="alert" className="form-error">Batches could not be loaded.</p>}
          {!batchesQuery.isPending && batches.length === 0 && <p style={{ margin: 0, color: colors.muted }}>No batches on record.</p>}
          {batches.map((batch) => (
            <div key={batch.batchId} style={{ display: 'flex', alignItems: 'baseline', gap: spacing.sm, fontSize: tokens.typography.sizes.sm, borderBottom: `1px solid ${colors.border}`, paddingBottom: spacing.xs }}>
              <span style={{ flex: 1 }}>
                <strong>{batch.batchNumber}</strong>
                <br />
                <span style={{ color: colors.muted }}>
                  {batch.expiryDate ?? 'no expiry'} · cost ৳{batch.unitCost}
                </span>
              </span>
              <span style={{ color: batch.expired ? colors.danger : colors.foreground }}>{batch.available} left</span>
              {batch.expired && Number(batch.available) > 0 && (
                <button
                  type="button"
                  className="quiet-action danger-action"
                  style={{ minHeight: 28, padding: `${spacing.xs} ${spacing.sm}`, fontSize: tokens.typography.sizes.sm }}
                  disabled={adjust.isPending}
                  onClick={() => disposeBatch(batch)}
                >
                  Dispose
                </button>
              )}
            </div>
          ))}
        </section>

        <section style={{ display: 'grid', gap: spacing.sm }}>
          <h3 style={{ margin: 0, fontSize: tokens.typography.sizes.md }}>Correct stock</h3>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: spacing.sm }}>
            <label style={label}>
              Signed quantity
              <input
                className="field"
                inputMode="decimal"
                placeholder="-2 or 5"
                value={quantity}
                onChange={(event) => setQuantity(decimalEntry(event.target.value))}
              />
            </label>
            <label style={label}>
              Batch (optional)
              <select className="field" value={batchId} onChange={(event) => setBatchId(event.target.value)}>
                <option value="">Whole product</option>
                {batches.map((batch) => <option key={batch.batchId} value={batch.batchId}>{batch.batchNumber}</option>)}
              </select>
            </label>
          </div>
          <label style={label}>
            Reason
            <input
              className="field"
              placeholder="Damp carton, count correction, expiry…"
              value={reason}
              onChange={(event) => setReason(event.target.value)}
            />
          </label>
          <label style={{ ...label, flexDirection: 'row', alignItems: 'center' }}>
            <input type="checkbox" checked={damage} onChange={(event) => setDamage(event.target.checked)} />
            <span>Damage / write-off (records a damage movement)</span>
          </label>
          <button type="button" className="primary-action" disabled={adjust.isPending} onClick={submitAdjustment}>
            {adjust.isPending ? 'Booking…' : 'Apply correction'}
          </button>
          {error && <p role="alert" className="form-error" style={{ margin: 0 }}>{error}</p>}
          {notice && <p role="status" style={{ margin: 0, color: colors.success }}>{notice}</p>}
        </section>

        <section style={{ display: 'grid', gap: spacing.xs }}>
          <h3 style={{ margin: 0, fontSize: tokens.typography.sizes.md }}>Recent movements</h3>
          {movementsQuery.isPending && <p style={{ margin: 0, color: colors.muted }}>Loading history…</p>}
          {movementsQuery.isError && <p role="alert" className="form-error">History could not be loaded.</p>}
          {movements.map((row) => (
            <div key={row.id} style={{ display: 'flex', gap: spacing.sm, fontSize: tokens.typography.sizes.sm, borderBottom: `1px solid ${colors.border}`, paddingBottom: spacing.xs }}>
              <span style={{ width: 92, color: colors.muted }}>{new Date(row.occurredAt).toLocaleDateString()}</span>
              <span style={{ width: 84 }}>{MOVEMENT_LABELS[row.movementType] ?? row.movementType}</span>
              <span style={{ color: Number(row.quantity) < 0 ? colors.danger : colors.success, width: 64 }}>{Number(row.quantity) > 0 ? '+' : ''}{Number(row.quantity)}</span>
              <span style={{ flex: 1, color: colors.muted }}>
                {row.reason ?? row.batchNumber ?? ''}
              </span>
            </div>
          ))}
          {!movementsQuery.isPending && movements.length === 0 && <p style={{ margin: 0, color: colors.muted }}>Nothing has moved yet.</p>}
        </section>

        <footer style={{ display: 'flex', justifyContent: 'space-between', gap: spacing.sm }}>
          <button type="button" className="quiet-action" style={quiet} onClick={onReceive}>Receive stock</button>
          <button type="button" className="primary-action" onClick={onClose}>Done</button>
        </footer>
      </div>
    </div>
  );
}

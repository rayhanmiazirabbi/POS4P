'use client';

import { useQuery, useQueryClient } from '@tanstack/react-query';
import type { Sale, SaleItem, SaleReturn } from '@pharmacy/api';
import type { Role } from '@pharmacy/types';
import { colors, spacing, tokens } from '@pharmacy/design-tokens';
import { useEffect, useMemo, useState, type KeyboardEvent, type ReactNode } from 'react';

import { pharmacyApi } from '@/lib/api';
import { decimalEntry } from '@/lib/numeric-input';

/**
 * Recent sales, returns and voids -- the counter's correction tools.
 *
 * Everything here is an online operation on purpose. A return or a void moves
 * stock and money on the server's say-so (the server recomputes the refund from
 * shelf prices and enforces the per-product return cap), so there is no offline
 * queue for it: the honest answer offline is "wait for the connection", not a
 * correction recorded on one terminal that the ledger never sees.
 *
 * Return quantities are capped at what this sale's lines show. Prior partial
 * returns are not part of the sale payload, so the client cannot pre-deduct
 * them; the server refuses an over-return and its message is shown in place.
 */
export function SalesHistoryDialog({
  role,
  onClose,
  onStockChanged,
}: {
  role: Role;
  onClose: () => void;
  /** Called after a return or void lands, so the till's prices and stock refresh. */
  onStockChanged: () => void;
}): ReactNode {
  const queryClient = useQueryClient();
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [filter, setFilter] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<SaleReturn | null>(null);
  const [busy, setBusy] = useState(false);
  const mayVoid = role === 'owner' || role === 'manager';

  const salesQuery = useQuery({
    queryKey: ['pos', 'sales', 'recent'],
    queryFn: async () => (await pharmacyApi.sales.list(undefined, { limit: 25 })).items,
    staleTime: 0,
  });

  useEffect(() => {
    const onKey = (event: globalThis.KeyboardEvent): void => {
      if (event.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  const sales = salesQuery.data ?? [];
  const selected = useMemo(() => sales.find((sale) => sale.id === selectedId) ?? null, [sales, selectedId]);
  const needle = filter.trim().toLowerCase();
  const visible = needle === ''
    ? sales
    : sales.filter((sale) =>
        (sale.receiptNumber ?? '').toLowerCase().includes(needle)
        || sale.items.some((item) => item.productName.toLowerCase().includes(needle)),
      );

  function refresh(): void {
    void queryClient.invalidateQueries({ queryKey: ['pos', 'sales', 'recent'] });
    onStockChanged();
  }

  function backToList(): void {
    setSelectedId(null);
    setResult(null);
    setError(null);
  }

  const dialog = selected === null
    ? <SalesList sales={visible} loading={salesQuery.isLoading} problem={salesQuery.isError} filter={filter} onFilter={setFilter} onSelect={setSelectedId} onClose={onClose} />
    : <SaleDetail
        sale={selected}
        mayVoid={mayVoid}
        busy={busy}
        error={error}
        result={result}
        setBusy={setBusy}
        setError={setError}
        setResult={setResult}
        onDone={refresh}
        onBack={backToList}
        onClose={onClose}
      />;

  return (
    <div className="dialog-backdrop" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
      <section
        className="dialog-panel"
        role="dialog"
        aria-modal="true"
        aria-labelledby="sales-history-title"
        style={{ width: 'min(560px, 100%)' }}
      >
        {dialog}
      </section>
    </div>
  );
}

function SalesList({
  sales,
  loading,
  problem,
  filter,
  onFilter,
  onSelect,
  onClose,
}: {
  sales: readonly Sale[];
  loading: boolean;
  problem: boolean;
  filter: string;
  onFilter: (value: string) => void;
  onSelect: (saleId: string) => void;
  onClose: () => void;
}): ReactNode {
  return (
    <>
      <header className="dialog-header">
        <div>
          <span className="eyebrow">Corrections</span>
          <h2 id="sales-history-title">Recent sales</h2>
          <p style={{ margin: '4px 0 0', color: colors.muted, fontSize: tokens.typography.sizes.sm }}>
            The last 25 sales on this branch. Open one to return items or void it.
          </p>
        </div>
        <button type="button" className="quiet-action" onClick={onClose}>Close</button>
      </header>
      <input
        className="field"
        style={{ margin: `${spacing.md} 0 ${spacing.sm}` }}
        autoFocus
        aria-label="Filter by receipt number or medicine"
        placeholder="Receipt number or medicine name"
        value={filter}
        onChange={(event) => onFilter(event.target.value)}
      />
      {loading && <p className="status-message status-message--muted">Loading recent sales…</p>}
      {problem && <p role="alert" className="status-message status-message--error">Could not load recent sales. Check the connection and reopen.</p>}
      {!loading && !problem && sales.length === 0 && (
        <p className="status-message status-message--muted">No sales match. Only the 25 most recent are listed.</p>
      )}
      <ul style={{ listStyle: 'none', margin: 0, padding: 0, display: 'flex', flexDirection: 'column', gap: spacing.xs }}>
        {sales.map((sale) => (
          <li key={sale.id}>
            <SaleRow sale={sale} onSelect={onSelect} />
          </li>
        ))}
      </ul>
    </>
  );
}

function SaleRow({ sale, onSelect }: { sale: Sale; onSelect: (saleId: string) => void }): ReactNode {
  const statusLabel = sale.status === 'completed' ? 'Completed' : sale.status === 'refunded' ? 'Refunded' : 'Voided';
  const statusColor = sale.status === 'completed' ? colors.muted : sale.status === 'refunded' ? colors.warning : colors.danger;
  const itemCount = sale.items.reduce((sum, item) => sum + Number(item.quantity), 0);
  return (
    <button
      type="button"
      onClick={() => onSelect(sale.id)}
      style={{
        width: '100%', display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', gap: spacing.sm,
        padding: `${spacing.sm} ${spacing.md}`, background: colors.surface, border: `1px solid ${colors.border}`,
        borderRadius: 8, cursor: 'pointer', textAlign: 'left',
      }}
    >
      <span style={{ display: 'flex', flexDirection: 'column', minWidth: 0 }}>
        <strong>{sale.receiptNumber ?? 'No receipt number'}</strong>
        <small style={{ color: statusColor }}>{statusLabel} · {itemCount} item{itemCount === 1 ? '' : 's'} · {new Date(sale.createdAt).toLocaleString()}</small>
      </span>
      <strong>৳{sale.total}</strong>
    </button>
  );
}

function SaleDetail({
  sale,
  mayVoid,
  busy,
  error,
  result,
  setBusy,
  setError,
  setResult,
  onDone,
  onBack,
  onClose,
}: {
  sale: Sale;
  mayVoid: boolean;
  busy: boolean;
  error: string | null;
  result: SaleReturn | null;
  setBusy: (value: boolean) => void;
  setError: (value: string | null) => void;
  setResult: (value: SaleReturn | null) => void;
  onDone: () => void;
  onBack: () => void;
  onClose: () => void;
}): ReactNode {
  // Quantity-to-return per sale line, keyed by line id. '' means none selected.
  const [quantities, setQuantities] = useState<Record<string, string>>({});
  const [reason, setReason] = useState('');
  const [voidReason, setVoidReason] = useState('');
  const [voidArmed, setVoidArmed] = useState(false);
  const offline = typeof navigator !== 'undefined' && !navigator.onLine;
  const returnable = sale.status === 'completed';
  const selectedLines = useMemo(
    () => Object.entries(quantities).filter(([, value]) => value.trim() !== '' && Number(value) > 0),
    [quantities],
  );
  const refundEstimate = useMemo(() => estimateRefund(sale.items, quantities), [sale.items, quantities]);

  function setQuantity(item: SaleItem, raw: string): void {
    const cleaned = decimalEntry(raw);
    const quantity = Number(cleaned);
    // Clamped to the line's sold quantity: anything higher is a refusal the
    // server would make anyway, and an entry the cashier cannot see is wrong.
    const capped = quantity > Number(item.quantity) ? String(Number(item.quantity)) : cleaned;
    setQuantities((current) => ({ ...current, [item.id]: capped }));
  }

  async function submitReturn(): Promise<void> {
    if (selectedLines.length === 0) { setError('Enter a quantity to return on at least one line.'); return; }
    if (reason.trim() === '') { setError('A reason is required for every return.'); return; }
    if (!navigator.onLine) { setError('Returns need an internet connection. Reconnect and retry.'); return; }
    setBusy(true); setError(null);
    try {
      const response = await pharmacyApi.sales.createReturn(sale.id, {
        reason: reason.trim(),
        lines: selectedLines.map(([saleItemId, quantity]) => ({ saleItemId, quantity })),
      });
      setResult(response.data);
      onDone();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Return failed');
    } finally {
      setBusy(false);
    }
  }

  async function submitVoid(): Promise<void> {
    if (voidReason.trim() === '') { setError('A reason is required to void a sale.'); return; }
    if (!navigator.onLine) { setError('Voiding needs an internet connection. Reconnect and retry.'); return; }
    if (!voidArmed) { setVoidArmed(true); return; }
    setBusy(true); setError(null);
    try {
      await pharmacyApi.sales.void(sale.id, { reason: voidReason.trim() });
      onDone();
      onClose();
    } catch (cause) {
      setVoidArmed(false);
      setError(cause instanceof Error ? cause.message : 'Void failed');
    } finally {
      setBusy(false);
    }
  }

  function onPanelKeyDown(event: KeyboardEvent): void {
    if (event.key === 'Escape') {
      // Stop the window-level listener, or one Escape both backs out of the
      // detail and closes the dialog around it.
      event.preventDefault();
      event.stopPropagation();
      if (voidArmed) setVoidArmed(false);
      else onBack();
    }
  }

  if (result !== null) {
    const refunded = Math.abs(Number(result.total)).toFixed(2);
    const restored = Number(result.advanceRestored);
    return (
      <div onKeyDown={onPanelKeyDown}>
        <header className="dialog-header">
          <div>
            <span className="eyebrow">Return recorded</span>
            <h2>৳{refunded} refunded</h2>
          </div>
        </header>
        <div style={{ padding: `${spacing.md} 0`, display: 'flex', flexDirection: 'column', gap: spacing.xs, color: colors.muted, fontSize: tokens.typography.sizes.sm }}>
          <span>Receipt {sale.receiptNumber ?? '—'} · stock is back on the shelf.</span>
          {restored > 0 && <span>৳{result.advanceRestored} went back to the customer&rsquo;s advance balance instead of cash.</span>}
          <span>Reason: {result.reason}</span>
        </div>
        <footer style={{ display: 'flex', justifyContent: 'flex-end', gap: spacing.sm }}>
          <button type="button" className="primary-action" onClick={onBack}>Back to recent sales</button>
        </footer>
      </div>
    );
  }

  return (
    <div onKeyDown={onPanelKeyDown}>
      <header className="dialog-header">
        <div>
          <span className="eyebrow">Corrections</span>
          <h2>{sale.receiptNumber ?? 'Sale'}</h2>
          <p style={{ margin: '4px 0 0', color: colors.muted, fontSize: tokens.typography.sizes.sm }}>
            {new Date(sale.createdAt).toLocaleString()} · total ৳{sale.total}
            {sale.status !== 'completed' && ` · ${sale.status === 'refunded' ? 'already refunded' : 'voided — no further corrections'}`}
          </p>
        </div>
        <button type="button" className="quiet-action" onClick={onBack}>Back</button>
      </header>

      {offline && <p role="status" className="status-message status-message--warning" style={{ marginTop: spacing.md }}>Offline. Returns and voids need a connection.</p>}

      <table style={{ width: '100%', borderCollapse: 'collapse', margin: `${spacing.md} 0 ${spacing.sm}`, fontSize: tokens.typography.sizes.sm }}>
        <caption className="visually-hidden">Items on this sale</caption>
        <thead>
          <tr style={{ textAlign: 'left', color: colors.muted }}>
            <th scope="col" style={{ padding: spacing.xs }}>Item</th>
            <th scope="col" style={{ padding: spacing.xs }}>Sold</th>
            <th scope="col" style={{ padding: spacing.xs }}>Line</th>
            <th scope="col" style={{ padding: spacing.xs }}>Return qty</th>
          </tr>
        </thead>
        <tbody>
          {sale.items.map((item) => (
            <tr key={item.id} style={{ borderBottom: `1px solid ${colors.border}` }}>
              <td style={{ padding: spacing.xs }}>
                <strong>{item.productName}</strong>
                <small style={{ display: 'block', color: colors.muted }}>৳{item.unitPrice} each</small>
              </td>
              <td style={{ padding: spacing.xs }}>{item.quantity}</td>
              <td style={{ padding: spacing.xs }}>৳{item.lineTotal}</td>
              <td style={{ padding: spacing.xs }}>
                <input
                  className="field"
                  style={{ width: 72, minHeight: 30, textAlign: 'right' }}
                  aria-label={`Quantity to return for ${item.productName}`}
                  inputMode="numeric"
                  placeholder="0"
                  disabled={!returnable || busy}
                  value={quantities[item.id] ?? ''}
                  onChange={(event) => setQuantity(item, event.target.value)}
                />
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      <label style={{ fontSize: tokens.typography.sizes.sm, display: 'flex', flexDirection: 'column', gap: spacing.xs, marginBottom: spacing.sm }}>
        Return reason
        <input
          className="field"
          aria-label="Return reason"
          placeholder="Why these items are coming back"
          maxLength={240}
          value={reason}
          disabled={!returnable || busy}
          onChange={(event) => setReason(event.target.value)}
        />
      </label>
      {refundEstimate !== null && selectedLines.length > 0 && (
        <p style={{ margin: `0 0 ${spacing.sm}`, color: colors.muted, fontSize: tokens.typography.sizes.sm }}>
          Estimated refund ৳{refundEstimate} — the server confirms the exact amount.
        </p>
      )}
      <button type="button" className="primary-action" disabled={!returnable || busy || offline} onClick={() => void submitReturn()}>
        {busy ? 'Returning…' : 'Return items'}
      </button>

      {mayVoid && (
        <section style={{ marginTop: spacing.lg, paddingTop: spacing.md, borderTop: `1px solid ${colors.border}` }}>
          <strong style={{ fontSize: tokens.typography.sizes.sm }}>Void the whole sale</strong>
          <p style={{ margin: `${spacing.xs} 0 ${spacing.sm}`, color: colors.muted, fontSize: tokens.typography.sizes.sm }}>
            Same-day sales only, and never a sale that already has a return against it. All items go back to stock and the sale leaves today&rsquo;s revenue.
          </p>
          <textarea
            className="field field--textarea"
            aria-label="Void reason"
            placeholder="Why this sale is being voided"
            maxLength={2000}
            value={voidReason}
            disabled={!returnable || busy}
            onChange={(event) => { setVoidReason(event.target.value); setVoidArmed(false); }}
          />
          <button
            type="button"
            className="quiet-action danger-action"
            style={{ marginTop: spacing.sm }}
            disabled={!returnable || busy || offline || voidReason.trim() === ''}
            onClick={() => void submitVoid()}
          >
            {voidArmed ? 'Confirm — void this sale' : 'Void sale'}
          </button>
        </section>
      )}

      {error !== null && <p role="alert" className="form-error" style={{ marginTop: spacing.sm }}>{error}</p>}
    </div>
  );
}

/**
 * Rough refund for the screen only: line net per unit times the entered
 * quantity. A global discount was spread across lines by the server in a way
 * this payload no longer carries, so the true figure can only come back from
 * the return call itself -- which is why every use is labelled an estimate.
 */
function estimateRefund(items: readonly SaleItem[], quantities: Record<string, string>): string | null {
  let total = 0;
  let any = false;
  for (const item of items) {
    const raw = quantities[item.id];
    if (raw === undefined || raw.trim() === '') continue;
    const quantity = Number(raw);
    if (!Number.isFinite(quantity) || quantity <= 0) continue;
    const unit = Number(item.quantity) === 0 ? 0 : Number(item.lineTotal) / Number(item.quantity);
    total += unit * quantity;
    any = true;
  }
  return any ? total.toFixed(2) : null;
}

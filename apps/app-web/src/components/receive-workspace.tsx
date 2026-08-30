'use client';

import { useQuery, useQueryClient } from '@tanstack/react-query';
import type { PurchaseReceiveRequest, Supplier } from '@pharmacy/api';
import type { ConfiguredPaymentMethod } from '@pharmacy/types';
import { useEffect, useMemo, useRef, useState, type KeyboardEvent, type ReactNode } from 'react';

import { pharmacyApi } from '@/lib/api';
import { decimalEntry } from '@/lib/numeric-input';
import { defaultReceiptConfig, loadEffectiveReceiptConfig } from '@/lib/receipt';
import { receiveLineAmounts, receiveTotals, useReceiveDrafts, type ReceiveCostMode, type ReceiveDraftLine } from '@/lib/receive-drafts';
import { useSession } from '@/lib/session';
import { MedicineFinder, type MedicineSelection } from './medicine-finder';
import { PurchaseReceiptDialog, type PrintablePurchaseReceipt } from './purchase-receipt-dialog';
import { ShiftPanel } from './shift-panel';

function id(): string { return globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random().toString(16).slice(2)}`; }
function baseLine(input: { identity: ReceiveDraftLine['identity']; name: string; sku?: string | null | undefined; unit?: string | null | undefined; shelf?: ReceiveDraftLine['shelf'] }): ReceiveDraftLine {
  return { id: id(), identity: input.identity, ...(input.shelf ? { shelf: input.shelf } : {}), name: input.name, sku: input.sku ?? '', unit: input.unit ?? 'unit', quantity: '1', costMode: 'unit', unitCost: '', lineTotal: '', batchNumber: '', expiryDate: '' };
}

export function ReceiveWorkspace({ modeSwitch }: { modeSwitch: ReactNode }): ReactNode {
  const { user } = useSession();
  const queryClient = useQueryClient();
  const draft = useReceiveDrafts((state) => state.active);
  const held = useReceiveDrafts((state) => state.held);
  const status = useReceiveDrafts((state) => state.status);
  const recoveryError = useReceiveDrafts((state) => state.recoveryError);
  const notice = useReceiveDrafts((state) => state.notice);
  const hydrate = useReceiveDrafts((state) => state.hydrate);
  const update = useReceiveDrafts((state) => state.updateActive);
  const hold = useReceiveDrafts((state) => state.holdActive);
  const resume = useReceiveDrafts((state) => state.resumeHeld);
  const deleteHeld = useReceiveDrafts((state) => state.deleteHeld);
  const clear = useReceiveDrafts((state) => state.clearActive);
  const reset = useReceiveDrafts((state) => state.resetCorruptStorage);
  const flush = useReceiveDrafts((state) => state.flush);
  const [cash, setCash] = useState('');
  const [digital, setDigital] = useState('');
  const [digitalMethod, setDigitalMethod] = useState('');
  const [digitalReference, setDigitalReference] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [review, setReview] = useState(false);
  const [setup, setSetup] = useState<Exclude<MedicineSelection, { kind: 'local' }> | null>(null);
  const [detailForId, setDetailForId] = useState<string | null>(null);
  const [printable, setPrintable] = useState<PrintablePurchaseReceipt | null>(null);

  const storeId = user?.storeId ?? null;
  useEffect(() => { if (user?.organizationId && storeId) void hydrate(user.organizationId, storeId); }, [hydrate, storeId, user?.organizationId]);
  useEffect(() => { const onHide = (): void => { void flush(); }; window.addEventListener('pagehide', onHide); return () => window.removeEventListener('pagehide', onHide); }, [flush]);

  const shelfQuery = useQuery({ queryKey: ['receive', 'shelf', storeId], enabled: storeId !== null, queryFn: async () => (await pharmacyApi.products.listCurrentStoreProducts()).items, staleTime: 30_000 });
  const supplierQuery = useQuery({ queryKey: ['receive', 'suppliers'], queryFn: async () => (await pharmacyApi.suppliers.list({ limit: 100 })).items, staleTime: 30_000 });
  const settingsQuery = useQuery({ queryKey: ['organization', 'settings'], queryFn: async () => (await pharmacyApi.organizations.readSettings()).data.settings, staleTime: 60_000 });
  const cashSession = useQuery({ queryKey: ['pos', 'cash-session'], queryFn: async () => (await pharmacyApi.cashSessions.current()).data, staleTime: 5_000 });
  const receiptSettings = useQuery({
    queryKey: ['receipt-config', user?.organizationId, storeId], enabled: Boolean(user?.organizationId && storeId), staleTime: 60_000,
    queryFn: async () => {
      let locale = settingsQuery.data?.locale ?? 'en-BD'; let timezone = settingsQuery.data?.defaultTimezone ?? 'Asia/Dhaka'; let storeName = user?.storeName ?? '';
      const config = await loadEffectiveReceiptConfig(user!.organizationId, storeId as string, async () => {
        const [store, organization] = await Promise.all([pharmacyApi.stores.readCurrent(), pharmacyApi.organizations.readSettings()]);
        locale = organization.data.settings.locale; timezone = store.data.timezone; storeName = store.data.name;
        return { store: store.data.settings, organization: organization.data.settings };
      });
      return { config, locale, timezone, storeName };
    },
  });
  const suppliers = (supplierQuery.data ?? []).filter((supplier) => supplier.status === 'active');
  const products = (shelfQuery.data ?? []).map((row) => ({ ...row, barcode: row.barcode ?? null, rack: row.rack ?? null }));
  const digitalMethods = useMemo(() => (settingsQuery.data?.paymentMethods ?? []).filter((method) => method.active), [settingsQuery.data]);
  useEffect(() => { if (digitalMethods.length === 0) { setDigitalMethod(''); setDigital(''); return; } if (!digitalMethods.some((entry) => entry.value === digitalMethod)) setDigitalMethod(digitalMethods[0]!.value); }, [digitalMethod, digitalMethods]);

  const totals = receiveTotals(draft.lines, draft.supplierTotal);
  const uncostedLines = draft.lines.filter((line) => !receiveLineAmounts(line).hasCost).length;
  const paid = (Number(cash || 0) + Number(digital || 0)).toFixed(2);
  const credit = Math.max(Number(totals.total) - Number(paid), 0).toFixed(2);
  const overpaid = Number(paid) > Number(totals.total);
  const online = typeof navigator === 'undefined' || navigator.onLine;
  const validLines = draft.lines.length > 0 && draft.lines.every((line) => receiveLineAmounts(line).valid);
  const canReview = draft.supplierId !== '' && validLines && totals.valid && !overpaid && (Number(cash || 0) === 0 || cashSession.data != null) && online;
  const activeMethodLabel = digitalMethods.find((method) => method.value === digitalMethod)?.label ?? digitalMethod;

  function resetTender(): void { setCash(''); setDigital(''); setDigitalReference(''); }
  function addLine(line: ReceiveDraftLine): void { update((current) => ({ ...current, lines: [...current.lines, line] })); setError(null); }
  function chooseMedicine(selection: MedicineSelection): void {
    if (selection.kind === 'local') { addLine(baseLine({ identity: { storeProductId: selection.item.id }, name: selection.item.name, sku: selection.item.sku, unit: selection.item.unit })); return; }
    if (selection.kind === 'catalog' && selection.item.shopStatus === 'on_shelf' && selection.item.storeProductId) {
      addLine(baseLine({ identity: { storeProductId: selection.item.storeProductId }, name: selection.item.name, sku: selection.item.sku, unit: selection.item.packageUnit })); return;
    }
    setSetup(selection);
  }
  function patchLine(lineId: string, change: Partial<ReceiveDraftLine>): void { update((current) => ({ ...current, lines: current.lines.map((line) => line.id === lineId ? { ...line, ...change } : line) })); }
  function holdReceipt(): void { if (!hold()) { setError('Add a product before holding this receipt.'); return; } resetTender(); setReview(false); }

  async function post(): Promise<void> {
    if (!canReview || !user) return;
    setBusy(true); setError(null);
    const request: PurchaseReceiveRequest = {
      supplierId: draft.supplierId,
      ...(draft.invoiceNumber.trim() ? { invoiceNumber: draft.invoiceNumber.trim() } : {}),
      ...(draft.purchasedAt ? { purchasedAt: draft.purchasedAt } : {}),
      ...(draft.supplierTotal.trim() ? { totalAmount: draft.supplierTotal } : {}),
      ...(draft.note.trim() ? { note: draft.note.trim() } : {}),
      items: draft.lines.map((line) => {
        const enteredCost = line.costMode === 'unit' ? line.unitCost : line.lineTotal;
        return {
          ...line.identity, ...(line.shelf ? { shelf: line.shelf } : {}), quantity: line.quantity,
          ...(enteredCost.trim() === '' ? {} : line.costMode === 'unit' ? { unitCost: line.unitCost } : { lineTotal: line.lineTotal }),
          ...(line.batchNumber.trim() ? { batchNumber: line.batchNumber.trim() } : {}),
          ...(line.expiryDate ? { expiryDate: line.expiryDate } : {}),
        };
      }),
      payments: [
        ...(Number(cash || 0) > 0 ? [{ method: 'cash', amount: Number(cash).toFixed(2) }] : []),
        ...(Number(digital || 0) > 0 ? [{ method: digitalMethod, amount: Number(digital).toFixed(2), ...(digitalReference.trim() ? { providerReference: digitalReference.trim() } : {}) }] : []),
      ],
    };
    try {
      const response = await pharmacyApi.purchases.receive(request, { idempotencyKey: id() });
      setPrintable({ receipt: response.data, config: receiptSettings.data?.config ?? defaultReceiptConfig, organizationName: user.organizationName, storeName: receiptSettings.data?.storeName ?? user.storeName ?? '', staffName: user.user.displayName, locale: receiptSettings.data?.locale ?? 'en-BD', timezone: receiptSettings.data?.timezone ?? 'Asia/Dhaka' });
      clear(); resetTender(); setReview(false);
      await Promise.all([queryClient.invalidateQueries({ queryKey: ['receive'] }), queryClient.invalidateQueries({ queryKey: ['inventory'] }), queryClient.invalidateQueries({ queryKey: ['pos', 'shelf'] }), queryClient.invalidateQueries({ queryKey: ['pos', 'cash-session'] }), queryClient.invalidateQueries({ queryKey: ['purchasing'] })]);
    } catch (cause) { setError(cause instanceof Error ? cause.message : 'Could not post this supplier receipt'); }
    finally { setBusy(false); }
  }

  if (storeId === null) return <main className="page-shell"><p className="status-message status-message--error">Choose a branch before receiving stock.</p></main>;
  if (status === 'idle' || status === 'loading') return <main className="page-shell"><p className="status-message status-message--muted">Restoring receiving carts…</p></main>;
  if (status === 'corrupt') return <main className="page-shell"><section className="surface surface-section"><h1>Saved receipts need attention</h1><p className="form-error">{recoveryError}</p><button type="button" className="primary-action" onClick={() => void reset()}>Reset receiving carts</button></section></main>;

  return <>
    <main className="split-grid split-grid--counter receive-workspace">
      <section className="surface pos-shelf receive-search-pane">
        <header className="pos-section-header"><div><span className="eyebrow">Supplier delivery</span><h1>Find received products</h1></div><span className="keyboard-hint">Online posting</span></header>
        <MedicineFinder products={products} actionLabel="Add to receipt" autoFocus onSelect={chooseMedicine} />
        {shelfQuery.isError && <p role="alert" className="form-error">Could not load this branch’s shelf.</p>}
      </section>
      <aside className="pos-rail"><section className="surface pos-cart receive-cart">
        <header className="cart-header"><div className="cart-title"><span className="cart-title-icon" aria-hidden="true">↓</span><div><h2>Receiving cart <span>({draft.lines.length} {draft.lines.length === 1 ? 'item' : 'items'})</span></h2><small>Saved automatically on this terminal</small></div></div><div className="cart-header-actions"><button type="button" className="quiet-action" disabled={draft.lines.length === 0} onClick={holdReceipt}>Hold</button><button type="button" className="quiet-action danger-action" disabled={draft.lines.length === 0} onClick={() => { clear(); resetTender(); }}>Clear</button></div></header>
        {notice && <p role="status" className="status-message cart-notice">{notice}</p>}
        <ul className="cart-lines receive-compact-lines">{draft.lines.map((line, index) => <ReceiveLineRow key={line.id} line={line} index={index} detailOpen={detailForId === line.id} onDetail={(open) => setDetailForId(open ? line.id : null)} onPatch={(change) => patchLine(line.id, change)} onRemove={() => update((current) => ({ ...current, lines: current.lines.filter((entry) => entry.id !== line.id) }))} />)}{draft.lines.length === 0 && <li className="cart-empty"><span aria-hidden="true">↓</span><strong>No products received yet</strong><small>Search or scan a product to start.</small></li>}</ul>

        <section className="cart-totals" aria-label="Receiving totals">
          <div className="summary-row"><span>Entered item costs</span><strong>৳{totals.enteredTotal}</strong></div>
          <div className="summary-row summary-row--editable receive-total-entry"><label htmlFor="supplier-total">Supplier total</label><span className="money-input"><span>৳</span><input id="supplier-total" aria-label="Supplier total" inputMode="decimal" placeholder={totals.enteredTotal} value={draft.supplierTotal} onChange={(event) => update((current) => ({ ...current, supplierTotal: decimalEntry(event.target.value) }))} /></span></div>
          {Number(totals.unallocated) > 0 && <div className="summary-row summary-row--warning"><span>Not assigned to item costs</span><strong>৳{totals.unallocated}</strong></div>}
          <div className="summary-total"><span>Receipt total</span><strong>৳{totals.total}</strong></div>
        </section>
        {!totals.valid && <p role="alert" className="form-error receive-inline-error">Supplier total cannot be less than entered item costs.</p>}
        {uncostedLines > 0 && <p role="status" className="status-message status-message--warning">{uncostedLines} {uncostedLines === 1 ? 'item has' : 'items have'} no cost and will enter inventory at ৳0.00. This affects margins, valuation, and supplier-return credit.</p>}

        <SupplierCombobox suppliers={suppliers} selectedId={draft.supplierId} selectedName={draft.supplierName} onSelect={(supplier) => { update((current) => ({ ...current, supplierId: supplier.id, supplierName: supplier.name })); setError(null); }} onClear={() => update((current) => ({ ...current, supplierId: '', supplierName: '' }))} onError={setError} />
        {supplierQuery.isError && <p role="alert" className="form-error receive-inline-error">Could not load suppliers. Retry in a moment.</p>}
        <div className="receive-document-meta"><label>Supplier invoice<input className="field" value={draft.invoiceNumber} onChange={(event) => update((current) => ({ ...current, invoiceNumber: event.target.value }))} placeholder="Optional" /></label><label>Received date<input className="field" type="date" value={draft.purchasedAt} onChange={(event) => update((current) => ({ ...current, purchasedAt: event.target.value }))} /></label></div>

        <section className="payment-section" aria-label="Supplier payment"><label className="payment-field"><span>Cash paid</span><span className="payment-input"><ReceiveIcon name="cash" /><input inputMode="decimal" value={cash} onChange={(event) => setCash(decimalEntry(event.target.value))} placeholder={totals.total} /></span></label>{digitalMethods.length > 0 && <label className="payment-field"><span>Digital ({activeMethodLabel}) amount</span><span className="payment-input"><ReceiveIcon name="phone" /><input inputMode="decimal" value={digital} onChange={(event) => setDigital(decimalEntry(event.target.value))} placeholder="0.00" /></span></label>}</section>
        {digitalMethods.length > 0 && <><div className="payment-methods" aria-label="Digital payment method">{digitalMethods.map((method: ConfiguredPaymentMethod) => <button type="button" key={method.value} aria-pressed={method.value === digitalMethod} className={method.value === digitalMethod ? 'payment-method payment-method--active' : 'payment-method'} onClick={() => setDigitalMethod(method.value)}><span className={`payment-mark${method.value === 'nagad' ? ' payment-mark--nagad' : ''}`} aria-hidden="true">{method.value === 'bkash' ? '➤' : '●'}</span>{method.label}</button>)}</div>{Number(digital || 0) > 0 && <input className="field" placeholder="Digital transaction reference (optional)" value={digitalReference} onChange={(event) => setDigitalReference(event.target.value)} />}</>}
        <div className="tender-summary"><span><ReceiveIcon name="wallet" /> Cash ৳{Number(cash || 0).toFixed(2)} {activeMethodLabel !== '' && <><i>·</i> {activeMethodLabel} ৳{Number(digital || 0).toFixed(2)}</>}</span><strong className={credit === '0.00' ? '' : 'has-due'}>Supplier due ৳{credit}</strong></div>
        {Number(cash || 0) > 0 && cashSession.data == null && <p className="status-message status-message--warning">Open a cash shift before posting this cash payment.</p>}
        {overpaid && <p role="alert" className="form-error receive-inline-error">Payments cannot exceed the receipt total.</p>}
        {!online && <p role="alert" className="status-message status-message--warning">Reconnect to post. This receipt remains saved locally.</p>}
        <button type="button" className="primary-action complete-sale" disabled={!canReview || busy} onClick={() => setReview(true)}>{busy ? 'Posting…' : 'Review receipt'}</button>
        {error && <p role="alert" className="form-error receive-inline-error">{error}</p>}
      </section></aside>
    </main>
    <nav className="held-tabstrip" aria-label="Held receiving carts"><div className="held-strip-label"><strong>Held receipts</strong><span className="held-strip-count">{held.length}</span></div>{held.length === 0 ? <p className="held-strip-empty">Held supplier receipts appear here.</p> : <ul className="held-tabs">{held.map((entry) => <li className="held-tab receive-held-tab" key={entry.id}><button type="button" className="held-main" onClick={() => { resume(entry.id); resetTender(); }}><span><strong>{entry.label}</strong><small>{entry.draft.lines.length} lines · ৳{receiveTotals(entry.draft.lines, entry.draft.supplierTotal).total}</small></span></button><button type="button" className="line-remove" aria-label={`Delete held receipt ${entry.label}`} onClick={() => deleteHeld(entry.id)}>×</button></li>)}</ul>}{modeSwitch}<ShiftPanel onError={setError} /></nav>
    {setup && <ProductSetupDrawer selection={setup} onClose={() => setSetup(null)} onAdd={(line) => { addLine(line); setSetup(null); }} />}
    {review && <ReviewDialog supplier={draft.supplierName} invoice={draft.invoiceNumber} lines={draft.lines.length} uncostedLines={uncostedLines} total={totals.total} paid={paid} credit={credit} busy={busy} onCancel={() => setReview(false)} onConfirm={() => void post()} />}
    {printable && <PurchaseReceiptDialog printable={printable} onClose={() => setPrintable(null)} />}
  </>;
}

function ReceiveLineRow({ line, index, detailOpen, onDetail, onPatch, onRemove }: { line: ReceiveDraftLine; index: number; detailOpen: boolean; onDetail: (open: boolean) => void; onPatch: (change: Partial<ReceiveDraftLine>) => void; onRemove: () => void }): ReactNode {
  const amounts = receiveLineAmounts(line);
  const detailRef = useRef<HTMLDivElement>(null);
  const expiryRef = useRef<HTMLInputElement>(null);
  const [popoverAbove, setPopoverAbove] = useState(false);
  useEffect(() => {
    if (!detailOpen) return;
    const tools = detailRef.current?.getBoundingClientRect();
    setPopoverAbove(tools != null && window.innerHeight - tools.bottom < 300);
    expiryRef.current?.focus();
    const closeOutside = (event: MouseEvent): void => { if (!detailRef.current?.contains(event.target as Node)) onDetail(false); };
    document.addEventListener('mousedown', closeOutside);
    return () => document.removeEventListener('mousedown', closeOutside);
  }, [detailOpen, onDetail]);
  function quantity(delta: number): void { const current = Number(line.quantity); onPatch({ quantity: String(Math.max((Number.isFinite(current) ? current : 0) + delta, 1)) }); }
  function switchCostMode(costMode: ReceiveCostMode): void {
    if (costMode === line.costMode) return;
    if (!amounts.valid || !amounts.hasCost) { onPatch({ costMode, unitCost: '', lineTotal: '' }); return; }
    onPatch(costMode === 'unit' ? { costMode, unitCost: amounts.unitCost, lineTotal: '' } : { costMode, unitCost: '', lineTotal: amounts.lineTotal });
  }
  return <li className="cart-adjustment-row receive-adjustment-row"><span className="line-number" aria-hidden="true">{index + 1}</span><span className="line-details"><strong>{line.name}</strong><small>{line.sku || 'New shelf item'} · {line.unit}{!amounts.hasCost ? ' · no cost' : ` · ৳${amounts.unitCost} / ${line.unit}`}</small></span><div className="quantity-stepper" aria-label={`Quantity for ${line.name}`}><button type="button" tabIndex={-1} aria-label={`Decrease ${line.name} quantity`} disabled={Number(line.quantity) <= 1} onClick={() => quantity(-1)}>−</button><input type="number" min="0.0001" step="any" inputMode="decimal" aria-label={`Quantity for ${line.name}`} value={line.quantity} onChange={(event) => onPatch({ quantity: decimalEntry(event.target.value) })} /><button type="button" tabIndex={-1} aria-label={`Increase ${line.name} quantity`} onClick={() => quantity(1)}>+</button></div><div className="line-discount-control receive-cost-control"><select tabIndex={-1} aria-label={`Cost entry type for ${line.name}`} value={line.costMode} onChange={(event) => switchCostMode(event.target.value as ReceiveCostMode)}><option value="unit">Per unit</option><option value="line">Line total</option></select><input aria-label={`${line.costMode === 'unit' ? 'Unit cost' : 'Line total'} for ${line.name}`} inputMode="decimal" placeholder="Optional" value={line.costMode === 'unit' ? line.unitCost : line.lineTotal} onChange={(event) => onPatch(line.costMode === 'unit' ? { unitCost: decimalEntry(event.target.value) } : { lineTotal: decimalEntry(event.target.value) })} /></div><strong className={`line-total${amounts.hasCost ? '' : ' line-total--muted'}`}>৳{amounts.valid ? amounts.lineTotal : '—'}</strong><div className="receive-line-tools" ref={detailRef}><button type="button" tabIndex={-1} className={`line-calendar${line.expiryDate || line.batchNumber ? ' line-calendar--active' : ''}`} aria-label={`Expiry and batch for ${line.name}`} aria-expanded={detailOpen} onClick={() => onDetail(!detailOpen)}><ReceiveIcon name="calendar" /></button>{detailOpen && <div className={`receive-detail-popover${popoverAbove ? ' receive-detail-popover--above' : ''}`} role="dialog" aria-label={`Expiry and batch for ${line.name}`} onKeyDown={(event) => { if (event.key === 'Escape') { event.preventDefault(); onDetail(false); } }}><label>Expiry date<input ref={expiryRef} className="field" type="date" value={line.expiryDate} onChange={(event) => onPatch({ expiryDate: event.target.value })} /></label><label>Batch number<input className="field" placeholder="Generated if blank" value={line.batchNumber} onChange={(event) => onPatch({ batchNumber: event.target.value })} /></label><button type="button" className="quiet-action" onClick={() => onDetail(false)}>Done</button></div>}</div><button type="button" tabIndex={-1} className="line-remove" aria-label={`Remove ${line.name}`} onClick={onRemove}>×</button></li>;
}

function SupplierCombobox({ suppliers, selectedId, selectedName, onSelect, onClear, onError }: { suppliers: readonly Supplier[]; selectedId: string; selectedName: string; onSelect: (supplier: Supplier) => void; onClear: () => void; onError: (message: string) => void }): ReactNode {
  const queryClient = useQueryClient();
  const [query, setQuery] = useState('');
  const [creating, setCreating] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const rowRefs = useRef<(HTMLButtonElement | null)[]>([]);
  const term = query.trim();
  const online = typeof navigator === 'undefined' || navigator.onLine;
  const shown = useMemo(() => suppliers.filter((supplier) => supplier.name.toLowerCase().includes(term.toLowerCase())).slice(0, 8), [suppliers, term]);
  const exact = shown.find((supplier) => supplier.name.localeCompare(term, undefined, { sensitivity: 'accent' }) === 0);
  const canCreate = online && term.length >= 2 && exact === undefined;
  const rowCount = shown.length + (canCreate ? 1 : 0);
  function choose(supplier: Supplier): void { setQuery(''); onSelect(supplier); }
  async function createSupplier(): Promise<void> {
    if (!canCreate) return;
    setCreating(true);
    try { const response = await pharmacyApi.suppliers.create({ name: term }); queryClient.setQueryData<readonly Supplier[]>(['receive', 'suppliers'], (current) => [...(current ?? []), response.data]); choose(response.data); }
    catch (cause) { onError(cause instanceof Error ? cause.message : 'Could not add that supplier'); }
    finally { setCreating(false); }
  }
  function submit(): void { const first = shown[0]; if (first) choose(first); else void createSupplier(); }
  function rowKeyDown(event: KeyboardEvent<HTMLButtonElement>, index: number): void { if (event.key === 'ArrowDown' && rowCount > 0) { event.preventDefault(); rowRefs.current[(index + 1) % rowCount]?.focus(); } else if (event.key === 'ArrowUp') { event.preventDefault(); index === 0 ? inputRef.current?.focus() : rowRefs.current[index - 1]?.focus(); } else if (event.key === 'Escape') { event.preventDefault(); inputRef.current?.focus(); } }
  return <div className="customer-combobox supplier-combobox"><div className="customer-lookup"><span aria-hidden="true"><ReceiveIcon name="supplier" /></span><input ref={inputRef} placeholder="Supplier name" aria-label="Supplier name" aria-expanded={term !== ''} value={query} onChange={(event) => setQuery(event.target.value)} onKeyDown={(event) => { if (event.key === 'Enter') { event.preventDefault(); submit(); } else if (event.key === 'ArrowDown' && term !== '') { event.preventDefault(); rowRefs.current[0]?.focus(); } else if (event.key === 'Escape') setQuery(''); }} /><button type="button" tabIndex={-1} aria-label="Find supplier" disabled={term === ''} onClick={() => inputRef.current?.select()}><ReceiveIcon name="search" /></button>{selectedId !== '' && <button type="button" tabIndex={-1} className="selected-customer" onClick={onClear}>{selectedName} <span aria-hidden="true">×</span></button>}</div>{term !== '' && <div className="customer-suggest" role="listbox" aria-label="Supplier matches">{shown.map((supplier, index) => <button key={supplier.id} ref={(node) => { rowRefs.current[index] = node; }} type="button" role="option" tabIndex={-1} className="customer-suggest-row" onClick={() => choose(supplier)} onKeyDown={(event) => rowKeyDown(event, index)}><span><strong>{supplier.name}</strong>{supplier.phone && <small>{supplier.phone}</small>}</span></button>)}{canCreate && <button ref={(node) => { rowRefs.current[shown.length] = node; }} type="button" role="option" tabIndex={-1} className="customer-suggest-new" disabled={creating} onClick={() => void createSupplier()} onKeyDown={(event) => rowKeyDown(event, shown.length)}><strong>{creating ? 'Adding supplier…' : `Add “${term}” as new supplier`}</strong><span>Saved with its name; contact details can be added later</span></button>}{term.length < 2 && <p className="finder-note">Type at least two characters to add a supplier.</p>}{!online && <p className="finder-note" role="alert">Connect to add a new supplier.</p>}</div>}</div>;
}

function ProductSetupDrawer({ selection, onClose, onAdd }: { selection: Exclude<MedicineSelection, { kind: 'local' }>; onClose: () => void; onAdd: (line: ReceiveDraftLine) => void }): ReactNode {
  const catalog = selection.kind === 'catalog' ? selection.item : null;
  const [unit, setUnit] = useState(catalog?.packageUnit ?? 'unit'); const [salePrice, setSalePrice] = useState(catalog?.salePrice ?? catalog?.referenceUnitPrice ?? ''); const [sku, setSku] = useState(catalog?.sku ?? ''); const [barcode, setBarcode] = useState(catalog?.barcode ?? ''); const [rack, setRack] = useState(''); const [minimum, setMinimum] = useState('0');
  const name = selection.kind === 'custom' ? selection.name : selection.item.name;
  const ready = unit.trim() !== '' && /^\d+(\.\d{1,2})?$/.test(salePrice);
  function add(): void { if (!ready) return; const identity: ReceiveDraftLine['identity'] = selection.kind === 'custom' ? { customProduct: { name, unit: unit.trim(), ...(barcode.trim() ? { barcode: barcode.trim() } : {}) } } : catalog?.shopStatus === 'in_org' && catalog.pharmacyProductId ? { pharmacyProductId: catalog.pharmacyProductId } : { catalogProductId: catalog!.catalogProductId! }; onAdd(baseLine({ identity, name, sku, unit, shelf: { salePrice, ...(sku.trim() ? { sku: sku.trim() } : {}), ...(barcode.trim() ? { barcode: barcode.trim() } : {}), ...(rack.trim() ? { rack: rack.trim() } : {}), ...(minimum ? { minimumStock: minimum } : {}) } })); }
  return <div className="drawer-backdrop" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}><div className="intake-drawer" role="dialog" aria-modal="true"><header><div><span className="eyebrow">New shelf product</span><h2>{name}</h2><p>Set selling details before adding this delivery line.</p></div><button type="button" className="quiet-action" onClick={onClose}>Close</button></header><div className="drawer-fields"><label>Stock unit<input autoFocus className="field" value={unit} onChange={(event) => setUnit(event.target.value)} /></label><label>Selling price<input className="field" inputMode="decimal" value={salePrice} onChange={(event) => setSalePrice(decimalEntry(event.target.value))} /></label><div className="field-pair"><label>SKU<input className="field" placeholder="Generated if blank" value={sku} onChange={(event) => setSku(event.target.value)} /></label><label>Rack<input className="field" value={rack} onChange={(event) => setRack(event.target.value)} /></label></div><label>Barcode<input className="field" value={barcode} onChange={(event) => setBarcode(event.target.value)} /></label><label>Minimum stock<input className="field" inputMode="decimal" value={minimum} onChange={(event) => setMinimum(decimalEntry(event.target.value))} /></label><button type="button" className="primary-action" disabled={!ready} onClick={add}>Add to receipt</button></div></div></div>;
}

function ReviewDialog({ supplier, invoice, lines, uncostedLines, total, paid, credit, busy, onCancel, onConfirm }: { supplier: string; invoice: string; lines: number; uncostedLines: number; total: string; paid: string; credit: string; busy: boolean; onCancel: () => void; onConfirm: () => void }): ReactNode {
  return <div className="dialog-backdrop" onMouseDown={(event) => { if (event.target === event.currentTarget) onCancel(); }}><section className="dialog-panel receive-review" role="dialog" aria-modal="true"><header className="dialog-header"><div><span className="eyebrow">Final review</span><h2>Post supplier receipt?</h2></div><button type="button" className="icon-action" onClick={onCancel}>×</button></header><dl><div><dt>Supplier</dt><dd>{supplier}</dd></div>{invoice && <div><dt>Invoice</dt><dd>{invoice}</dd></div>}<div><dt>Received lines</dt><dd>{lines}</dd></div><div><dt>Total</dt><dd>৳{total}</dd></div><div><dt>Paid now</dt><dd>৳{paid}</dd></div><div><dt>Supplier credit</dt><dd>৳{credit}</dd></div></dl>{uncostedLines > 0 && <p className="status-message status-message--warning">{uncostedLines} {uncostedLines === 1 ? 'item will' : 'items will'} be valued at ৳0.00, including for future supplier returns.</p>}<p>This posts stock, supplier balance, and payments together. It cannot be edited afterward.</p><footer><button type="button" className="quiet-action" onClick={onCancel}>Back</button><button autoFocus type="button" className="primary-action" disabled={busy} onClick={onConfirm}>{busy ? 'Posting…' : 'Post and generate voucher'}</button></footer></section></div>;
}

function ReceiveIcon({ name }: { name: 'cash' | 'phone' | 'wallet' | 'calendar' | 'supplier' | 'search' }): ReactNode {
  const common = { className: 'pos-icon', viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', strokeLinecap: 'round' as const, strokeLinejoin: 'round' as const };
  switch (name) {
    case 'cash': return <svg {...common}><rect x="3" y="6" width="18" height="12" rx="2" /><circle cx="12" cy="12" r="2.5" /><path d="M6 9h.01M18 15h.01" /></svg>;
    case 'phone': return <svg {...common}><rect x="7" y="2.5" width="10" height="19" rx="2" /><path d="M10 5h4M11 18.5h2" /></svg>;
    case 'wallet': return <svg {...common}><path d="M4 7.5V6a2 2 0 0 1 2-2h12v4M4 7.5h15a2 2 0 0 1 2 2V18a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2Z" /><path d="M16 13h5" /></svg>;
    case 'calendar': return <svg {...common}><rect x="4" y="5" width="16" height="15" rx="2" /><path d="M8 3v4M16 3v4M4 10h16" /></svg>;
    case 'supplier': return <svg {...common}><path d="M4 20V8l8-4 8 4v12M8 20v-5h8v5M8 10h.01M12 10h.01M16 10h.01" /></svg>;
    case 'search': return <svg {...common}><circle cx="10.5" cy="10.5" r="5.5" /><path d="m15 15 4 4" /></svg>;
  }
}

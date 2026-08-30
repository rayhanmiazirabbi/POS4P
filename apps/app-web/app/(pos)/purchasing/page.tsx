'use client';

import { useQuery, useQueryClient } from '@tanstack/react-query';
import type { Purchase, PurchaseOrder, PurchaseOrderItem, PurchaseOrderStatusWire, ReorderSuggestion, ShelfItem, Supplier } from '@pharmacy/api';
import { can } from '@pharmacy/permissions';
import { usePathname, useRouter, useSearchParams } from 'next/navigation';
import { Suspense, useEffect, useMemo, useRef, useState, type ReactNode, type RefObject } from 'react';
import { createPortal } from 'react-dom';

import { MedicineFinder, type MedicineSelection } from '@/components/medicine-finder';
import { PurchaseReceiptDialog, type PrintablePurchaseReceipt } from '@/components/purchase-receipt-dialog';
import { pharmacyApi } from '@/lib/api';
import { decimalEntry } from '@/lib/numeric-input';
import { orderProgress, purchasingView, quantityText, statusLabel, type PurchasingView } from '@/lib/purchasing';
import { defaultReceiptConfig } from '@/lib/receipt';
import { useSession } from '@/lib/session';

const orderStatuses: readonly PurchaseOrderStatusWire[] = ['draft', 'ordered', 'partially_received', 'received', 'closed', 'cancelled'];
type OrderDraftLine = { id: string; name: string; quantity: string; estUnitCost: string; pharmacyProductId?: string; catalogProductId?: string };

function key(): string { return globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random().toString(16).slice(2)}`; }
function message(cause: unknown, fallback: string): string { return cause instanceof Error ? cause.message : fallback; }
function OverlayPortal({ children }: { children: ReactNode }): ReactNode {
  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);
  return mounted ? createPortal(children, document.body) : null;
}
function restoreFocus(target: HTMLButtonElement | null): void { window.setTimeout(() => target?.focus(), 0); }
function useDialogFocus(panelRef: RefObject<HTMLElement | null>): void {
  useEffect(() => {
    const panel = panelRef.current;
    if (panel === null) return;
    const selector = 'button:not(:disabled), input:not(:disabled), select:not(:disabled), textarea:not(:disabled), [href], [tabindex]:not([tabindex="-1"])';
    (panel.querySelector<HTMLElement>(selector) ?? panel).focus();
    function trapTab(event: KeyboardEvent): void {
      if (event.key !== 'Tab') return;
      const controls = Array.from(panel!.querySelectorAll<HTMLElement>(selector)).filter((node) => node.getAttribute('aria-hidden') !== 'true');
      if (controls.length === 0) { event.preventDefault(); panel!.focus(); return; }
      const first = controls[0]!; const last = controls.at(-1)!;
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    }
    panel.addEventListener('keydown', trapTab);
    return () => panel.removeEventListener('keydown', trapTab);
  }, [panelRef]);
}

export default function PurchasingPage(): ReactNode {
  return <Suspense fallback={<main className="page-shell"><p className="status-message status-message--muted">Loading purchasing workspace…</p></main>}><PurchasingWorkspace /></Suspense>;
}

function PurchasingWorkspace(): ReactNode {
  const { user } = useSession();
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const view = purchasingView(searchParams.get('view'));
  const [newOrderOpen, setNewOrderOpen] = useState(false);
  const newOrderTriggerRef = useRef<HTMLButtonElement>(null);
  const storeId = user?.storeId ?? null;
  const mayManagePurchases = user !== null && can(user.role, 'purchases.manage');
  const suppliersQuery = useQuery({ queryKey: ['purchasing', 'suppliers'], queryFn: async () => (await pharmacyApi.suppliers.list({ limit: 100 })).items, staleTime: 60_000 });
  const shelfQuery = useQuery({ queryKey: ['purchasing', 'shelf', storeId], enabled: storeId !== null, queryFn: async () => (await pharmacyApi.products.listCurrentStoreProducts()).items, staleTime: 30_000 });

  function selectView(next: PurchasingView): void {
    const params = new URLSearchParams(searchParams.toString());
    if (next !== view) ['q', 'status', 'supplier', 'from', 'to'].forEach((name) => params.delete(name));
    if (next === 'replenishment') params.delete('view'); else params.set('view', next);
    router.replace(`${pathname}${params.size ? `?${params.toString()}` : ''}`, { scroll: false });
  }
  if (storeId === null) return <main className="page-shell"><p className="status-message status-message--error">Choose a branch before managing purchasing.</p></main>;
  const suppliers = (suppliersQuery.data ?? []).filter((supplier) => supplier.status === 'active');
  const shelf = shelfQuery.data ?? [];
  return <>
    <main className="page-shell purchasing-workspace">
      <header className="page-heading purchasing-heading"><div><span className="eyebrow">Inbound stock</span><h1>Purchasing</h1><p>Plan replenishment, follow orders, and review supplier receipts.</p></div><button ref={newOrderTriggerRef} type="button" className="primary-action" onClick={() => setNewOrderOpen(true)}>New order</button></header>
      <nav className="workspace-tabs" aria-label="Purchasing views">
        <button type="button" aria-current={view === 'replenishment' ? 'page' : undefined} onClick={() => selectView('replenishment')}>Replenishment</button>
        <button type="button" aria-current={view === 'orders' ? 'page' : undefined} onClick={() => selectView('orders')}>Orders</button>
        <button type="button" aria-current={view === 'history' ? 'page' : undefined} onClick={() => selectView('history')}>Purchase history</button>
      </nav>
      {view === 'replenishment' && <ReplenishmentView storeId={storeId} suppliers={suppliers} onCreated={() => selectView('orders')} />}
      {view === 'orders' && <OrdersView suppliers={suppliers} shelf={shelf} />}
      {view === 'history' && <HistoryView suppliers={suppliers} mayManagePurchases={mayManagePurchases} />}
    </main>
    {newOrderOpen && <OverlayPortal><NewOrderDrawer suppliers={suppliers} shelf={shelf} onClose={() => { setNewOrderOpen(false); restoreFocus(newOrderTriggerRef.current); }} onCreated={() => { setNewOrderOpen(false); selectView('orders'); restoreFocus(newOrderTriggerRef.current); }} /></OverlayPortal>}
  </>;
}

function ReplenishmentView({ storeId, suppliers, onCreated }: { storeId: string; suppliers: readonly Supplier[]; onCreated: () => void }): ReactNode {
  const queryClient = useQueryClient();
  const [search, setSearch] = useState('');
  const [selected, setSelected] = useState<ReadonlySet<string>>(new Set());
  const [quantities, setQuantities] = useState<Record<string, string>>({});
  const [supplierId, setSupplierId] = useState('');
  const [expectedAt, setExpectedAt] = useState('');
  const [busy, setBusy] = useState(false);
  const [feedback, setFeedback] = useState<{ kind: 'error' | 'note'; text: string } | null>(null);
  const query = useQuery({ queryKey: ['inventory', 'reorder', storeId], queryFn: async () => (await pharmacyApi.inventory.reorderSuggestions(storeId)).data, staleTime: 30_000 });
  const items = query.data ?? [];
  useEffect(() => {
    if (items.length === 0) return;
    setSelected((current) => current.size === 0 ? new Set(items.map((item) => item.storeProductId)) : current);
    setQuantities((current) => Object.fromEntries(items.map((item) => [item.storeProductId, current[item.storeProductId] ?? item.suggestedQuantity])));
  }, [items]);
  const shown = useMemo(() => { const term = search.trim().toLowerCase(); return term === '' ? items : items.filter((item) => `${item.productName} ${item.sku}`.toLowerCase().includes(term)); }, [items, search]);
  const chosen = items.filter((item) => selected.has(item.storeProductId) && Number(quantities[item.storeProductId]) > 0);
  function toggle(id: string): void { setSelected((current) => { const next = new Set(current); if (next.has(id)) next.delete(id); else next.add(id); return next; }); }
  async function createDraft(): Promise<void> {
    if (chosen.length === 0) return;
    setBusy(true); setFeedback(null);
    try {
      const response = await pharmacyApi.purchaseOrders.create({ ...(supplierId ? { supplierId } : {}), ...(expectedAt ? { expectedAt } : {}), note: 'From reorder suggestions', items: chosen.map((item) => ({ name: item.productName, quantity: quantities[item.storeProductId]!, pharmacyProductId: item.pharmacyProductId })) }, { idempotencyKey: key() });
      setFeedback({ kind: 'note', text: `Draft ${response.data.id.slice(0, 8)} created with ${chosen.length} items.` });
      await queryClient.invalidateQueries({ queryKey: ['purchasing', 'purchase-orders'] }); onCreated();
    } catch (cause) { setFeedback({ kind: 'error', text: message(cause, 'Could not create the draft order') }); }
    finally { setBusy(false); }
  }
  return <section className="purchasing-stage" aria-labelledby="replenishment-title">
    <header className="workspace-section-heading"><div><h2 id="replenishment-title">Below minimum</h2><p>Review suggested quantities before creating a draft order.</p></div><span className="workspace-count">{items.length} items</span></header>
    <div className="purchasing-toolbar"><label className="search-field"><span className="sr-only">Search replenishment suggestions</span><input className="field" value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Search medicine or SKU" /></label><label><span>Supplier</span><select className="field" value={supplierId} onChange={(event) => setSupplierId(event.target.value)}><option value="">Assign later</option>{suppliers.map((supplier) => <option key={supplier.id} value={supplier.id}>{supplier.name}</option>)}</select></label><label><span>Expected</span><input className="field" type="date" value={expectedAt} onChange={(event) => setExpectedAt(event.target.value)} /></label></div>
    {query.isPending && <p className="empty-copy">Checking branch minimums…</p>}{query.isError && <p role="alert" className="status-message status-message--error">{message(query.error, 'Could not load reorder suggestions')}</p>}
    {!query.isPending && items.length === 0 && <div className="purchasing-empty"><strong>Stock levels look healthy</strong><span>Nothing is currently below its minimum.</span></div>}
    {items.length > 0 && <div className="responsive-table"><table className="purchasing-table"><thead><tr><th className="check-cell"><input type="checkbox" aria-label="Select all shown suggestions" checked={shown.length > 0 && shown.every((item) => selected.has(item.storeProductId))} onChange={(event) => setSelected((current) => { const next = new Set(current); shown.forEach((item) => event.target.checked ? next.add(item.storeProductId) : next.delete(item.storeProductId)); return next; })} /></th><th>Medicine</th><th>Available</th><th>Minimum</th><th className="quantity-cell">Order quantity</th></tr></thead><tbody>{shown.map((item) => <ReorderRow key={item.storeProductId} item={item} checked={selected.has(item.storeProductId)} quantity={quantities[item.storeProductId] ?? item.suggestedQuantity} onToggle={() => toggle(item.storeProductId)} onQuantity={(quantity) => setQuantities((current) => ({ ...current, [item.storeProductId]: quantity }))} />)}</tbody></table></div>}
    {feedback && <p role={feedback.kind === 'error' ? 'alert' : 'status'} className={`status-message${feedback.kind === 'error' ? ' status-message--error' : ''}`}>{feedback.text}</p>}
    {items.length > 0 && <footer className="selection-action"><span><strong>{chosen.length}</strong> selected for this order</span><button type="button" className="primary-action" disabled={chosen.length === 0 || busy} onClick={() => void createDraft()}>{busy ? 'Creating draft…' : 'Create draft order'}</button></footer>}
  </section>;
}

function ReorderRow({ item, checked, quantity, onToggle, onQuantity }: { item: ReorderSuggestion; checked: boolean; quantity: string; onToggle: () => void; onQuantity: (value: string) => void }): ReactNode {
  return <tr className={checked ? 'is-selected' : ''}><td className="check-cell"><input type="checkbox" checked={checked} aria-label={`Select ${item.productName}`} onChange={onToggle} /></td><td data-label="Medicine"><strong>{item.productName}</strong><small>{item.sku}</small></td><td data-label="Available" className="warning-value">{quantityText(item.available)}</td><td data-label="Minimum">{quantityText(item.minimumStock)}</td><td data-label="Order quantity" className="quantity-cell"><input className="field" aria-label={`Order quantity for ${item.productName}`} inputMode="decimal" value={quantity} onChange={(event) => onQuantity(decimalEntry(event.target.value))} /></td></tr>;
}

function OrdersView({ suppliers, shelf }: { suppliers: readonly Supplier[]; shelf: readonly ShelfItem[] }): ReactNode {
  const router = useRouter(); const pathname = usePathname(); const searchParams = useSearchParams();
  const status = orderStatuses.includes(searchParams.get('status') as PurchaseOrderStatusWire) ? searchParams.get('status') as PurchaseOrderStatusWire : '';
  const search = searchParams.get('q') ?? '';
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const inspectorTriggerRef = useRef<HTMLButtonElement | null>(null);
  const query = useQuery({ queryKey: ['purchasing', 'purchase-orders', status], queryFn: async () => (await pharmacyApi.purchaseOrders.list(status ? { status } : {}, { limit: 100 })).items, staleTime: 15_000 });
  const orders = useMemo(() => { const term = search.trim().toLowerCase(); return (query.data ?? []).filter((order) => term === '' || `${order.supplierName ?? ''} ${order.note ?? ''} ${order.id}`.toLowerCase().includes(term)); }, [query.data, search]);
  function setFilter(name: string, value: string): void { const params = new URLSearchParams(searchParams.toString()); if (value) params.set(name, value); else params.delete(name); router.replace(`${pathname}?${params.toString()}`, { scroll: false }); }
  function closeInspector(): void { setSelectedId(null); restoreFocus(inspectorTriggerRef.current); }
  return <section className="purchasing-stage" aria-labelledby="orders-title">
    <header className="workspace-section-heading"><div><h2 id="orders-title">Purchase orders</h2><p>Draft, place, and follow orders through delivery.</p></div><span className="workspace-count">{orders.length} shown</span></header>
    <div className="purchasing-toolbar purchasing-toolbar--orders"><label className="search-field"><span className="sr-only">Search purchase orders</span><input className="field" placeholder="Search supplier, note, or order ID" value={search} onChange={(event) => setFilter('q', event.target.value)} /></label><label><span>Status</span><select className="field" value={status} onChange={(event) => setFilter('status', event.target.value)}><option value="">All statuses</option>{orderStatuses.map((value) => <option key={value} value={value}>{statusLabel(value)}</option>)}</select></label></div>
    {query.isPending && <p className="empty-copy">Loading purchase orders…</p>}
    {query.isError && <p role="alert" className="status-message status-message--error">{message(query.error, 'Could not load purchase orders')}</p>}
    {!query.isPending && orders.length === 0 && <div className="purchasing-empty"><strong>No matching orders</strong><span>Adjust the filter or start a new order.</span></div>}
    {orders.length > 0 && <div className="responsive-table"><table className="purchasing-table order-table"><thead><tr><th>Order</th><th>Supplier</th><th>Status</th><th>Expected</th><th>Items</th><th>Received</th><th><span className="sr-only">Open</span></th></tr></thead><tbody>{orders.map((order) => <tr key={order.id}><td data-label="Order"><strong>PO-{order.id.slice(0, 8)}</strong><small>{order.createdAt.slice(0, 10)}</small></td><td data-label="Supplier">{order.supplierName ?? 'Not assigned'}</td><td data-label="Status"><StatusBadge status={order.status} /></td><td data-label="Expected">{order.expectedAt ?? '—'}</td><td data-label="Items">{order.itemCount}</td><td data-label="Received"><Progress value={orderProgress(order)} label={`${quantityText(order.receivedQuantity)} of ${quantityText(order.orderedQuantity)}`} /></td><td className="row-action"><button type="button" className="quiet-action" onClick={(event) => { inspectorTriggerRef.current = event.currentTarget; setSelectedId(order.id); }}>Open</button></td></tr>)}</tbody></table></div>}
    {selectedId && <OverlayPortal><OrderInspector orderId={selectedId} suppliers={suppliers} shelf={shelf} onClose={closeInspector} /></OverlayPortal>}
  </section>;
}

function StatusBadge({ status }: { status: PurchaseOrderStatusWire }): ReactNode { return <span className={`status-badge status-badge--${status}`}>{statusLabel(status)}</span>; }
function Progress({ value, label }: { value: number; label: string }): ReactNode { return <span className="order-progress"><span aria-hidden="true"><i style={{ width: `${value * 100}%` }} /></span><small>{label}</small></span>; }

function OrderInspector({ orderId, suppliers: _suppliers, shelf, onClose }: { orderId: string; suppliers: readonly Supplier[]; shelf: readonly ShelfItem[]; onClose: () => void }): ReactNode {
  const queryClient = useQueryClient(); const router = useRouter(); const panelRef = useRef<HTMLElement>(null); const [busy, setBusy] = useState(false); const [feedback, setFeedback] = useState<string | null>(null);
  const detail = useQuery({ queryKey: ['purchasing', 'purchase-order', orderId], queryFn: async () => (await pharmacyApi.purchaseOrders.read(orderId)).data }); const order = detail.data;
  useDialogFocus(panelRef);
  async function run(action: () => Promise<unknown>): Promise<void> { setBusy(true); setFeedback(null); try { await action(); await Promise.all([queryClient.invalidateQueries({ queryKey: ['purchasing', 'purchase-orders'] }), queryClient.invalidateQueries({ queryKey: ['purchasing', 'purchase-order', orderId] })]); } catch (cause) { setFeedback(message(cause, 'The order could not be updated')); } finally { setBusy(false); } }
  return <div className="drawer-backdrop" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}><section ref={panelRef} tabIndex={-1} className="purchase-inspector" role="dialog" aria-modal="true" aria-labelledby="order-inspector-title" onKeyDown={(event) => { if (event.key === 'Escape') onClose(); }}><header><div><span className="eyebrow">Purchase order</span><h2 id="order-inspector-title">PO-{orderId.slice(0, 8)}</h2>{order && <p>{order.supplierName ?? 'Supplier not assigned'} · {statusLabel(order.status)}</p>}</div><button type="button" className="icon-action" aria-label="Close order" onClick={onClose}>×</button></header>{detail.isPending && <p className="empty-copy">Loading order…</p>}{detail.isError && <p role="alert" className="status-message status-message--error">{message(detail.error, 'Could not load order')}</p>}{order && <><dl className="order-meta"><div><dt>Created</dt><dd>{order.createdAt.slice(0, 10)}</dd></div><div><dt>Expected</dt><dd>{order.expectedAt ?? 'Not set'}</dd></div><div><dt>Items</dt><dd>{order.itemCount}</dd></div><div><dt>Received</dt><dd>{quantityText(order.receivedQuantity)} / {quantityText(order.orderedQuantity)}</dd></div></dl>{order.note && <p className="order-note">{order.note}</p>}<section className="inspector-section"><div className="inspector-section-title"><h3>Order lines</h3><span>{order.items.length}</span></div><ul className="order-line-list">{order.items.map((item) => <OrderLineEditor key={item.id} order={order} item={item} busy={busy} onRun={run} />)}</ul>{order.status === 'draft' && <OrderLineAdder orderId={order.id} shelf={shelf} busy={busy} onRun={run} />}</section>{feedback && <p role="alert" className="status-message status-message--error">{feedback}</p>}<footer className="inspector-actions">{order.status === 'draft' && <button type="button" className="primary-action" disabled={busy || order.items.length === 0} onClick={() => void run(() => pharmacyApi.purchaseOrders.order(order.id))}>Place order</button>}{(order.status === 'ordered' || order.status === 'partially_received') && <button type="button" className="primary-action" disabled={busy} onClick={() => router.push(`/pos?mode=receive&purchaseOrderId=${order.id}`)}>Receive in POS</button>}{(order.status === 'ordered' || order.status === 'partially_received') && <button type="button" className="quiet-action" disabled={busy} onClick={() => void run(() => pharmacyApi.purchaseOrders.close(order.id))}>Close incomplete</button>}{(order.status === 'draft' || order.status === 'ordered') && <button type="button" className="quiet-action danger-action" disabled={busy} onClick={() => void run(() => pharmacyApi.purchaseOrders.cancel(order.id))}>Cancel order</button>}</footer></>}</section></div>;
}

function OrderLineEditor({ order, item, busy, onRun }: { order: PurchaseOrder; item: PurchaseOrderItem; busy: boolean; onRun: (action: () => Promise<unknown>) => Promise<void> }): ReactNode {
  const [quantity, setQuantity] = useState(item.quantity); const [cost, setCost] = useState(item.estUnitCost ?? ''); const changed = quantity !== item.quantity || cost !== (item.estUnitCost ?? '');
  return <li><div className="order-line-main"><strong>{item.name}</strong>{order.status === 'draft' ? <div className="order-line-edit"><label><span>Quantity</span><input className="field" inputMode="decimal" value={quantity} onChange={(event) => setQuantity(decimalEntry(event.target.value))} /></label><label><span>Est. cost</span><input className="field" inputMode="decimal" value={cost} onChange={(event) => setCost(decimalEntry(event.target.value))} placeholder="Optional" /></label></div> : <span>Ordered {quantityText(item.quantity)} · received {quantityText(item.receivedQuantity)} · remaining {quantityText(item.remainingQuantity)}</span>}</div>{order.status === 'draft' && <div className="order-line-buttons">{changed && <button type="button" className="quiet-action" disabled={busy || Number(quantity) <= 0} onClick={() => void onRun(() => pharmacyApi.purchaseOrders.updateItem(order.id, item.id, { quantity, estUnitCost: cost === '' ? null : cost }))}>Save</button>}<button type="button" className="line-remove" aria-label={`Remove ${item.name}`} disabled={busy} onClick={() => void onRun(() => pharmacyApi.purchaseOrders.removeItem(order.id, item.id))}>×</button></div>}</li>;
}

function lineBody(selection: MedicineSelection | null, shelf: readonly ShelfItem[], quantity: string, cost: string): { name: string; quantity: string; estUnitCost?: string; pharmacyProductId?: string; catalogProductId?: string } | null {
  if (selection === null || Number(quantity) <= 0) return null;
  if (selection.kind === 'local') { const row = shelf.find((entry) => entry.id === selection.item.id); return { name: selection.item.name, quantity, ...(cost ? { estUnitCost: cost } : {}), ...(row ? { pharmacyProductId: row.pharmacyProductId } : {}) }; }
  if (selection.kind === 'catalog') return { name: selection.item.name, quantity, ...(cost ? { estUnitCost: cost } : {}), ...(selection.item.pharmacyProductId ? { pharmacyProductId: selection.item.pharmacyProductId } : {}), ...(selection.item.catalogProductId ? { catalogProductId: selection.item.catalogProductId } : {}) };
  return { name: selection.name, quantity, ...(cost ? { estUnitCost: cost } : {}) };
}

function OrderLineAdder({ orderId, shelf, busy, onRun }: { orderId: string; shelf: readonly ShelfItem[]; busy: boolean; onRun: (action: () => Promise<unknown>) => Promise<void> }): ReactNode {
  const [selection, setSelection] = useState<MedicineSelection | null>(null); const [quantity, setQuantity] = useState('1'); const [cost, setCost] = useState(''); const line = lineBody(selection, shelf, quantity, cost);
  const products = shelf.map((row) => ({ ...row, barcode: row.barcode ?? null, rack: row.rack ?? null }));
  return <div className="order-line-adder"><h4>Add a line</h4>{selection === null ? <MedicineFinder products={products} actionLabel="Add to order" onSelect={setSelection} /> : <><div className="selected-product"><strong>{selection.kind === 'custom' ? selection.name : selection.item.name}</strong><button type="button" className="quiet-action" onClick={() => setSelection(null)}>Change</button></div><div className="order-line-edit"><label><span>Quantity</span><input className="field" inputMode="decimal" value={quantity} onChange={(event) => setQuantity(decimalEntry(event.target.value))} /></label><label><span>Est. cost</span><input className="field" inputMode="decimal" value={cost} onChange={(event) => setCost(decimalEntry(event.target.value))} placeholder="Optional" /></label></div><button type="button" className="quiet-action" disabled={busy || line === null} onClick={() => line && void onRun(async () => { await pharmacyApi.purchaseOrders.addItem(orderId, line); setSelection(null); setQuantity('1'); setCost(''); })}>Add line</button></>}</div>;
}

function NewOrderDrawer({ suppliers, shelf, onClose, onCreated }: { suppliers: readonly Supplier[]; shelf: readonly ShelfItem[]; onClose: () => void; onCreated: () => void }): ReactNode {
  const queryClient = useQueryClient(); const panelRef = useRef<HTMLElement>(null); const [supplierId, setSupplierId] = useState(''); const [expectedAt, setExpectedAt] = useState(''); const [note, setNote] = useState(''); const [lines, setLines] = useState<readonly OrderDraftLine[]>([]); const [selection, setSelection] = useState<MedicineSelection | null>(null); const [quantity, setQuantity] = useState('1'); const [cost, setCost] = useState(''); const [busy, setBusy] = useState(false); const [error, setError] = useState<string | null>(null);
  useDialogFocus(panelRef);
  function addSelection(): void { const line = lineBody(selection, shelf, quantity, cost); if (line === null) return; setLines((current) => [...current, { id: key(), estUnitCost: line.estUnitCost ?? '', ...line }]); setSelection(null); setQuantity('1'); setCost(''); }
  async function create(place: boolean): Promise<void> { setBusy(true); setError(null); try { const response = await pharmacyApi.purchaseOrders.create({ ...(supplierId ? { supplierId } : {}), ...(expectedAt ? { expectedAt } : {}), ...(note.trim() ? { note: note.trim() } : {}), items: lines.map(({ id: _id, estUnitCost, ...line }) => ({ ...line, ...(estUnitCost ? { estUnitCost } : {}) })) }, { idempotencyKey: key() }); if (place) await pharmacyApi.purchaseOrders.order(response.data.id); await queryClient.invalidateQueries({ queryKey: ['purchasing', 'purchase-orders'] }); onCreated(); } catch (cause) { setError(message(cause, 'Could not create the order')); } finally { setBusy(false); } }
  const products = shelf.map((row) => ({ ...row, barcode: row.barcode ?? null, rack: row.rack ?? null }));
  return <div className="drawer-backdrop" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}><section ref={panelRef} tabIndex={-1} className="purchase-inspector new-order-drawer" role="dialog" aria-modal="true" aria-labelledby="new-order-title" onKeyDown={(event) => { if (event.key === 'Escape') onClose(); }}><header><div><span className="eyebrow">Purchase order</span><h2 id="new-order-title">New order</h2><p>Add products now or save an empty draft for later.</p></div><button type="button" className="icon-action" aria-label="Close new order" onClick={onClose}>×</button></header><div className="new-order-meta"><label><span>Supplier</span><select className="field" value={supplierId} onChange={(event) => setSupplierId(event.target.value)}><option value="">Assign later</option>{suppliers.map((supplier) => <option key={supplier.id} value={supplier.id}>{supplier.name}</option>)}</select></label><label><span>Expected</span><input className="field" type="date" value={expectedAt} onChange={(event) => setExpectedAt(event.target.value)} /></label><label className="wide-field"><span>Note</span><textarea className="field field--textarea" value={note} onChange={(event) => setNote(event.target.value)} placeholder="Optional order note" /></label></div><section className="inspector-section"><div className="inspector-section-title"><h3>Order lines</h3><span>{lines.length}</span></div>{lines.length > 0 && <ul className="order-line-list">{lines.map((line) => <li key={line.id}><div className="order-line-main"><strong>{line.name}</strong><span>{quantityText(line.quantity)}{line.estUnitCost ? ` · ~৳${line.estUnitCost} each` : ''}</span></div><button type="button" className="line-remove" aria-label={`Remove ${line.name}`} onClick={() => setLines((current) => current.filter((entry) => entry.id !== line.id))}>×</button></li>)}</ul>}<div className="order-line-adder">{selection === null ? <MedicineFinder products={products} actionLabel="Select" onSelect={setSelection} /> : <><div className="selected-product"><strong>{selection.kind === 'custom' ? selection.name : selection.item.name}</strong><button type="button" className="quiet-action" onClick={() => setSelection(null)}>Change</button></div><div className="order-line-edit"><label><span>Quantity</span><input className="field" inputMode="decimal" value={quantity} onChange={(event) => setQuantity(decimalEntry(event.target.value))} /></label><label><span>Est. cost</span><input className="field" inputMode="decimal" value={cost} onChange={(event) => setCost(decimalEntry(event.target.value))} placeholder="Optional" /></label></div><button type="button" className="quiet-action" disabled={Number(quantity) <= 0} onClick={addSelection}>Add to order</button></>}</div></section>{error && <p role="alert" className="status-message status-message--error">{error}</p>}<footer className="inspector-actions"><button type="button" className="quiet-action" disabled={busy} onClick={() => void create(false)}>{busy ? 'Saving…' : 'Save draft'}</button><button type="button" className="primary-action" disabled={busy || lines.length === 0} onClick={() => void create(true)}>Place order</button></footer></section></div>;
}

function HistoryView({ suppliers, mayManagePurchases }: { suppliers: readonly Supplier[]; mayManagePurchases: boolean }): ReactNode {
  const { user } = useSession(); const queryClient = useQueryClient(); const router = useRouter(); const pathname = usePathname(); const searchParams = useSearchParams();
  const supplierId = searchParams.get('supplier') ?? ''; const statusParam = searchParams.get('status'); const status: '' | 'draft' | 'confirmed' | 'returned' = statusParam === 'draft' || statusParam === 'confirmed' || statusParam === 'returned' ? statusParam : ''; const purchasedFrom = searchParams.get('from') ?? ''; const purchasedTo = searchParams.get('to') ?? '';
  const [selected, setSelected] = useState<Purchase | null>(null); const [printable, setPrintable] = useState<PrintablePurchaseReceipt | null>(null); const [feedback, setFeedback] = useState<string | null>(null);
  const inspectorTriggerRef = useRef<HTMLButtonElement | null>(null);
  const query = useQuery({ queryKey: ['purchasing', 'purchases', supplierId, status, purchasedFrom, purchasedTo], queryFn: async () => (await pharmacyApi.purchases.list({ ...(supplierId ? { supplierId } : {}), ...(status ? { status } : {}), ...(purchasedFrom ? { purchasedFrom } : {}), ...(purchasedTo ? { purchasedTo } : {}) }, { limit: 100 })).items, staleTime: 15_000 });
  function setFilter(name: string, value: string): void { const params = new URLSearchParams(searchParams.toString()); if (value) params.set(name, value); else params.delete(name); router.replace(`${pathname}?${params.toString()}`, { scroll: false }); }
  function closeInspector(): void { setSelected(null); restoreFocus(inspectorTriggerRef.current); }
  async function confirm(purchaseId: string): Promise<void> { try { await pharmacyApi.purchases.confirm(purchaseId, { idempotencyKey: key() }); closeInspector(); await queryClient.invalidateQueries({ queryKey: ['purchasing'] }); } catch (cause) { setFeedback(message(cause, 'Could not confirm the purchase draft')); } }
  async function voucher(purchaseId: string): Promise<void> { try { const response = await pharmacyApi.purchases.receipt(purchaseId); setPrintable({ receipt: response.data, config: defaultReceiptConfig, organizationName: user?.organizationName ?? '', storeName: user?.storeName ?? '', staffName: user?.user.displayName ?? '', locale: 'en-BD', timezone: 'Asia/Dhaka' }); } catch (cause) { setFeedback(message(cause, 'Could not load the voucher')); } }
  const purchases = query.data ?? [];
  return <section className="purchasing-stage" aria-labelledby="history-title">
    <header className="workspace-section-heading"><div><h2 id="history-title">Purchase history</h2><p>Confirmed receipts, returns, and manager review drafts.</p></div><span className="workspace-count">{purchases.length} records</span></header>
    <div className="purchasing-toolbar purchasing-toolbar--history"><label><span>Supplier</span><select className="field" value={supplierId} onChange={(event) => setFilter('supplier', event.target.value)}><option value="">All suppliers</option>{suppliers.map((supplier) => <option key={supplier.id} value={supplier.id}>{supplier.name}</option>)}</select></label><label><span>From</span><input className="field" type="date" value={purchasedFrom} max={purchasedTo || undefined} onChange={(event) => setFilter('from', event.target.value)} /></label><label><span>To</span><input className="field" type="date" value={purchasedTo} min={purchasedFrom || undefined} onChange={(event) => setFilter('to', event.target.value)} /></label><label><span>Status</span><select className="field" value={status} onChange={(event) => setFilter('status', event.target.value)}><option value="">All statuses</option><option value="confirmed">Confirmed</option><option value="returned">Returned</option>{mayManagePurchases && <option value="draft">Needs review</option>}</select></label></div>
    {query.isPending && <p className="empty-copy">Loading purchase history…</p>}
    {query.isError && <p role="alert" className="status-message status-message--error">{message(query.error, 'Could not load purchase history')}</p>}
    {!query.isPending && purchases.length === 0 && <div className="purchasing-empty"><strong>No matching purchases</strong><span>Supplier receipts will appear here after posting.</span></div>}
    {purchases.length > 0 && <div className="responsive-table"><table className="purchasing-table"><thead><tr><th>Date</th><th>Receipt</th><th>Supplier</th><th>Status</th><th>Items</th><th>Total</th><th><span className="sr-only">Open</span></th></tr></thead><tbody>{purchases.map((purchase) => <tr key={purchase.id}><td data-label="Date">{purchase.purchasedAt}</td><td data-label="Receipt"><strong>{purchase.receiptNumber ?? `Draft ${purchase.id.slice(0, 8)}`}</strong>{purchase.purchaseOrderId && <small>PO-{purchase.purchaseOrderId.slice(0, 8)}</small>}</td><td data-label="Supplier">{purchase.supplierName ?? '—'}</td><td data-label="Status"><span className={`status-badge status-badge--${purchase.status}`}>{purchase.status === 'draft' ? 'Needs review' : purchase.status.replace(/^./, (letter) => letter.toUpperCase())}</span></td><td data-label="Items">{purchase.itemCount}</td><td data-label="Total">{purchase.totalAmount ? `৳${purchase.totalAmount}` : 'Restricted'}</td><td className="row-action"><button type="button" className="quiet-action" onClick={(event) => { inspectorTriggerRef.current = event.currentTarget; setSelected(purchase); }}>Open</button></td></tr>)}</tbody></table></div>}
    {feedback && <p role="alert" className="status-message status-message--error">{feedback}</p>}
    {selected && <OverlayPortal><PurchaseInspector purchase={selected} mayManagePurchases={mayManagePurchases} onClose={closeInspector} onConfirm={confirm} onVoucher={voucher} /></OverlayPortal>}
    {printable && <OverlayPortal><PurchaseReceiptDialog printable={printable} onClose={() => setPrintable(null)} /></OverlayPortal>}
  </section>;
}

function PurchaseInspector({ purchase, mayManagePurchases, onClose, onConfirm, onVoucher }: { purchase: Purchase; mayManagePurchases: boolean; onClose: () => void; onConfirm: (id: string) => Promise<void>; onVoucher: (id: string) => Promise<void> }): ReactNode {
  const panelRef = useRef<HTMLElement>(null);
  const detail = useQuery({ queryKey: ['purchasing', 'purchase', purchase.id], queryFn: async () => (await pharmacyApi.purchases.read(purchase.id)).data }); const row = detail.data ?? purchase;
  useDialogFocus(panelRef);
  return <div className="drawer-backdrop" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}><section ref={panelRef} tabIndex={-1} className="purchase-inspector" role="dialog" aria-modal="true" aria-labelledby="purchase-inspector-title" onKeyDown={(event) => { if (event.key === 'Escape') onClose(); }}>
    <header><div><span className="eyebrow">Supplier receipt</span><h2 id="purchase-inspector-title">{row.receiptNumber ?? `Draft ${row.id.slice(0, 8)}`}</h2><p>{row.supplierName ?? 'Supplier'} · {row.purchasedAt}</p></div><button type="button" className="icon-action" aria-label="Close purchase" onClick={onClose}>×</button></header>
    {detail.isPending && <p className="empty-copy">Loading receipt lines…</p>}
    {detail.isError && <p role="alert" className="status-message status-message--error">{message(detail.error, 'Could not load receipt details')}</p>}
    {detail.data && <><dl className="order-meta"><div><dt>Status</dt><dd>{row.status}</dd></div><div><dt>Invoice</dt><dd>{row.invoiceNumber ?? 'Not recorded'}</dd></div><div><dt>Items</dt><dd>{row.items.length}</dd></div><div><dt>Total</dt><dd>{row.totalAmount ? `৳${row.totalAmount}` : 'Restricted'}</dd></div></dl><ul className="order-line-list purchase-line-list">{row.items.map((item) => <li key={item.id}><div className="order-line-main"><strong>{item.batchNumber}</strong><span>Qty {quantityText(item.quantity)}{item.lineTotal ? ` · ৳${item.lineTotal}` : ''}{item.expiryDate ? ` · expires ${item.expiryDate}` : ''}</span></div></li>)}</ul><footer className="inspector-actions">{row.status === 'confirmed' && row.receiptNumber && <button type="button" className="primary-action" onClick={() => void onVoucher(row.id)}>Print or copy voucher</button>}{row.status === 'draft' && mayManagePurchases && <button type="button" className="primary-action" onClick={() => void onConfirm(row.id)}>Confirm legacy draft</button>}</footer></>}
  </section></div>;
}

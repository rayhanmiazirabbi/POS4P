'use client';

import { useQuery, useQueryClient } from '@tanstack/react-query';
import type { CatalogSearchItem, InventoryIntake, InventoryIntakeRequest, PurchaseOrder } from '@pharmacy/api';
import type { ShelfProduct } from '@pharmacy/sync';
import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react';

import { pharmacyApi } from '@/lib/api';
import { decimalEntry } from '@/lib/numeric-input';
import type { MedicineSelection } from './medicine-finder';

function newKey(): string {
  if (typeof crypto.randomUUID === 'function') return crypto.randomUUID();
  const words = new Uint32Array(4);
  crypto.getRandomValues(words);
  return `${Date.now()}-${Array.from(words, (word) => word.toString(16).padStart(8, '0')).join('')}-intake`;
}

function catalog(selection: MedicineSelection): CatalogSearchItem | null {
  return selection.kind === 'catalog' ? selection.item : null;
}

export function IntakeDrawer({
  selection,
  source,
  onClose,
  onSaved,
}: {
  selection: MedicineSelection;
  source: 'opening_stock' | 'supplier_receive';
  onClose: () => void;
  onSaved: (intake: InventoryIntake) => void;
}): ReactNode {
  const global = catalog(selection);
  const local = selection.kind === 'local' ? selection.item : null;
  const [quantity, setQuantity] = useState(source === 'opening_stock' ? '1' : '');
  const [salePrice, setSalePrice] = useState(local?.salePrice ?? global?.salePrice ?? global?.referenceUnitPrice ?? '');
  const [sku, setSku] = useState(local?.sku ?? global?.sku ?? '');
  const [barcode, setBarcode] = useState(local?.barcode ?? global?.barcode ?? '');
  const [rack, setRack] = useState(local?.rack ?? '');
  const [minimumStock, setMinimumStock] = useState('0');
  const [unitCost, setUnitCost] = useState('');
  const [batchNumber, setBatchNumber] = useState('');
  const [expiryDate, setExpiryDate] = useState('');
  const [supplierId, setSupplierId] = useState('');
  const [reference, setReference] = useState('');
  const [customUnit, setCustomUnit] = useState('tablet');
  const [advanced, setAdvanced] = useState(source === 'supplier_receive');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // The purchase-order side of the drawer: '' means a fresh draft, an id means
  // an existing one. The cashier decides whether stock walks in now or gets ordered.
  const [poTarget, setPoTarget] = useState('');
  const [orderedNote, setOrderedNote] = useState<string | null>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  const title = selection.kind === 'custom' ? selection.name : selection.item.name;
  const isExisting = selection.kind === 'local' || global?.shopStatus === 'on_shelf';
  const unit = selection.kind === 'local' ? selection.item.unit ?? 'unit' : global?.packageUnit ?? customUnit;
  const online = typeof navigator === 'undefined' || navigator.onLine;
  const queryClient = useQueryClient();

  const suppliers = useQuery({
    queryKey: ['suppliers', 'intake'],
    queryFn: async () => (await pharmacyApi.suppliers.list({ limit: 100 })).items,
    enabled: source === 'supplier_receive' && advanced,
    staleTime: 60_000,
  });

  const draftOrders = useQuery({
    queryKey: ['purchasing', 'purchase-orders', 'draft'],
    queryFn: async () => (await pharmacyApi.purchaseOrders.list({ status: 'draft' }, { limit: 20 })).items,
    enabled: source === 'opening_stock' && online,
    staleTime: 15_000,
  });

  // An open draft is the likelier destination than a new one, so it becomes the
  // default the moment the list lands. Only applied while untouched ('').
  useEffect(() => {
    if (poTarget === '' && (draftOrders.data ?? []).length > 0) setPoTarget(draftOrders.data![0]!.id);
  }, [draftOrders.data, poTarget]);

  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    panelRef.current?.querySelector<HTMLInputElement>('input')?.focus();
    return () => previous?.focus();
  }, []);

  const canSave = useMemo(() => {
    if (!/^\d+(\.\d+)?$/.test(quantity) || Number(quantity) <= 0) return false;
    if (!isExisting && !/^\d+(\.\d{1,2})?$/.test(salePrice)) return false;
    if (source === 'supplier_receive' && !/^\d+(\.\d{1,2})?$/.test(unitCost)) return false;
    return true;
  }, [isExisting, quantity, salePrice, source, unitCost]);

  async function save(): Promise<void> {
    if (!canSave || !online) return;
    setBusy(true); setError(null);
    const body: InventoryIntakeRequest = {
      source,
      quantity,
      ...(selection.kind === 'local' ? { storeProductId: selection.item.id } : {}),
      ...(selection.kind === 'custom' ? { customProduct: { name: selection.name, unit: customUnit, ...(barcode ? { barcode } : {}) } } : {}),
      ...(global?.shopStatus === 'on_shelf' && global.storeProductId ? { storeProductId: global.storeProductId } : {}),
      ...(global?.shopStatus === 'in_org' && global.pharmacyProductId ? { pharmacyProductId: global.pharmacyProductId } : {}),
      ...(global?.shopStatus === 'absent' && global.catalogProductId ? { catalogProductId: global.catalogProductId } : {}),
      shelf: {
        ...(salePrice ? { salePrice } : {}), ...(sku ? { sku } : {}), ...(barcode ? { barcode } : {}),
        ...(rack ? { rack } : {}), ...(minimumStock ? { minimumStock } : {}),
      },
      ...(unitCost ? { unitCost } : {}), ...(batchNumber ? { batchNumber } : {}),
      ...(expiryDate ? { expiryDate } : {}), ...(supplierId ? { supplierId } : {}),
      ...(reference.trim() ? { reference: reference.trim() } : {}),
    };
    try {
      const response = await pharmacyApi.inventory.intake(body, { idempotencyKey: newKey() });
      onSaved(response.data);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Could not save this stock');
    } finally { setBusy(false); }
  }

  const canOrder = /^\d+(\.\d+)?$/.test(quantity) && Number(quantity) > 0;

  /**
   * The other exit for an out-of-shelf medicine: no stock arrives, the line goes
   * on a purchase order instead. Catalog identity is carried when there is one,
   * so `to-purchase` can resolve the line to a product later.
   */
  async function addToPurchaseOrder(): Promise<void> {
    if (!canOrder || !online) return;
    setBusy(true); setError(null); setOrderedNote(null);
    const line = {
      name: title,
      quantity,
      ...(unitCost ? { estUnitCost: unitCost } : {}),
      ...(global?.catalogProductId ? { catalogProductId: global.catalogProductId } : {}),
      ...(global?.pharmacyProductId ? { pharmacyProductId: global.pharmacyProductId } : {}),
    };
    try {
      let orderId = poTarget;
      if (orderId === '') {
        const created = await pharmacyApi.purchaseOrders.create({ note: 'From POS out-of-stock' }, { idempotencyKey: newKey() });
        orderId = created.data.id;
        setPoTarget(orderId);
      }
      await pharmacyApi.purchaseOrders.addItem(orderId, line, { idempotencyKey: newKey() });
      await queryClient.invalidateQueries({ queryKey: ['purchasing', 'purchase-orders'] });
      const draft = (draftOrders.data ?? []).find((order: PurchaseOrder) => order.id === orderId);
      setOrderedNote(`Added to draft order ${orderId.slice(0, 8)}${draft ? ` (${draft.items.length + 1} lines)` : ''}.`);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Could not add to the purchase order');
    } finally { setBusy(false); }
  }

  return (
    <div className="drawer-backdrop" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
      <div
        ref={panelRef}
        className="intake-drawer"
        role="dialog"
        aria-modal="true"
        aria-labelledby="intake-title"
        onKeyDown={(event) => {
          if (event.key === 'Escape') { event.preventDefault(); onClose(); return; }
          if (event.key === 'Enter' && event.target instanceof HTMLElement && event.target.matches('input')) {
            event.preventDefault();
            if (canSave && !busy && online) void save();
            return;
          }
          if (event.key !== 'Tab') return;
          const focusable = Array.from(panelRef.current?.querySelectorAll<HTMLElement>('button:not([disabled]), input:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])') ?? []);
          const first = focusable[0];
          const last = focusable.at(-1);
          if (event.shiftKey && document.activeElement === first && last) { event.preventDefault(); last.focus(); }
          else if (!event.shiftKey && document.activeElement === last && first) { event.preventDefault(); first.focus(); }
        }}
      >
        <header><div><span className="eyebrow">{source === 'opening_stock' ? 'Add and sell' : 'Receive stock'}</span><h2 id="intake-title">{title}</h2><p>{unit} is the stock and selling unit.</p></div><button type="button" className="quiet-action" onClick={onClose}>Close</button></header>
        <div className="drawer-fields">
          {selection.kind === 'custom' && <label>Stock unit<input className="field" value={customUnit} onChange={(event) => setCustomUnit(event.target.value)} /></label>}
          <label>{source === 'opening_stock' ? 'Current stock' : 'Quantity received'} ({unit})<input className="field" inputMode="decimal" value={quantity} onChange={(event) => setQuantity(decimalEntry(event.target.value))} /></label>
          <label>Selling price per {unit}<input className="field" inputMode="decimal" placeholder={isExisting ? 'Keep current price' : 'Required'} value={salePrice} onChange={(event) => setSalePrice(decimalEntry(event.target.value))} /></label>
          {source === 'supplier_receive' && <label>Unit cost<input className="field" inputMode="decimal" value={unitCost} onChange={(event) => setUnitCost(decimalEntry(event.target.value))} /></label>}
          <div className="field-pair"><label>SKU<input className="field" placeholder="Generated if blank" value={sku} onChange={(event) => setSku(event.target.value)} /></label><label>Rack<input className="field" value={rack} onChange={(event) => setRack(event.target.value)} /></label></div>
          <label>Barcode<input className="field" value={barcode} onChange={(event) => setBarcode(event.target.value)} /></label>
          <button type="button" className="disclosure" onClick={() => setAdvanced((value) => !value)}>{advanced ? 'Hide details' : 'Batch, cost, supplier, and alerts'}</button>
          {advanced && <div className="advanced-fields">
            {source === 'opening_stock' && <label>Unit cost (optional)<input className="field" inputMode="decimal" placeholder="Unknown / 0.00" value={unitCost} onChange={(event) => setUnitCost(decimalEntry(event.target.value))} /></label>}
            <div className="field-pair"><label>Batch number<input className="field" placeholder="Generated if blank" value={batchNumber} onChange={(event) => setBatchNumber(event.target.value)} /></label><label>Expiry date<input className="field" type="date" value={expiryDate} onChange={(event) => setExpiryDate(event.target.value)} /></label></div>
            <label>Minimum stock alert<input className="field" inputMode="decimal" value={minimumStock} onChange={(event) => setMinimumStock(decimalEntry(event.target.value))} /></label>
            {source === 'supplier_receive' && <label>Supplier<select className="field" value={supplierId} onChange={(event) => setSupplierId(event.target.value)}><option value="">Not linked</option>{(suppliers.data ?? []).map((supplier) => <option key={supplier.id} value={supplier.id}>{supplier.name}</option>)}</select></label>}
            <label>Invoice / reference<input className="field" value={reference} onChange={(event) => setReference(event.target.value)} /></label>
          </div>}
        </div>
        {error && <p className="form-error" role="alert">{error}</p>}
        {orderedNote && <p className="finder-note" role="status">{orderedNote}</p>}
        {source === 'opening_stock' && (
          <div className="po-order-row">
            <label className="po-order-target">
              No stock now? Order it
              <select className="field" value={poTarget} onChange={(event) => setPoTarget(event.target.value)} disabled={busy}>
                <option value="">New draft order</option>
                {(draftOrders.data ?? []).map((order) => (
                  <option key={order.id} value={order.id}>Draft {order.id.slice(0, 8)} · {order.items.length} lines</option>
                ))}
              </select>
            </label>
            <button
              type="button"
              className="quiet-action"
              disabled={!canOrder || busy || !online}
              title={!canOrder ? 'Enter a quantity first' : undefined}
              onClick={() => void addToPurchaseOrder()}
            >
              {busy ? 'Adding…' : 'Add to purchase order'}
            </button>
          </div>
        )}
        {!online && <p className="form-error" role="alert">Connect before adding or receiving a new medicine.</p>}
        <footer><button type="button" className="quiet-action" onClick={onClose}>Cancel</button><button type="button" className="primary-action" disabled={!canSave || busy || !online} onClick={() => void save()}>{busy ? 'Saving…' : source === 'opening_stock' ? 'Add stock and sell' : 'Receive stock'}</button></footer>
      </div>
    </div>
  );
}

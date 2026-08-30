'use client';

import { useQuery, useQueryClient } from '@tanstack/react-query';
import type { DiscountInput, InventoryIntake, SaleChargeInput, SaleCreateRequest } from '@pharmacy/api';
import {
  calculateSaleTotals,
  provisionalReceipt,
  receiptFromSale,
  splitTender,
  tenderPayments,
  validateSalePayments,
  wirePayments,
  type Receipt as SaleReceipt,
} from '@pharmacy/sales';
import { money, multiply, type MoneyValue } from '@pharmacy/money';
import { colors, spacing, tokens } from '@pharmacy/design-tokens';
import { can } from '@pharmacy/permissions';
import {
  buildGroupedShelfView,
  describeMedicineMatch,
  envelopeContextFor,
  findMedicineAlternatives,
  highlightMedicineSpans,
  loadShelf,
  medicineMatchesAreFuzzy,
  mergeMedicineAlternatives,
  submitShelfEntry,
  type RankedMedicine,
  type ShelfLoad,
  type ShelfProduct,
  type StuckEntry,
} from '@pharmacy/sync';
import { useCallback, useEffect, useMemo, useRef, useState, type CSSProperties, type ReactNode } from 'react';

import { pharmacyApi } from '@/lib/api';
import { CustomerCombobox } from '@/components/customer-combobox';
import { IntakeDrawer } from '@/components/intake-drawer';
import { MedicineFinder, type MedicineSelection } from '@/components/medicine-finder';
import { ReceiptDialog } from '@/components/receipt-dialog';
import { ReceiveWorkspace } from '@/components/receive-workspace';
import { SalesHistoryDialog } from '@/components/sales-history-dialog';
import { ShiftPanel } from '@/components/shift-panel';
import { amountDueNow, calculateCheckout } from '@/lib/checkout';
import { decimalEntry } from '@/lib/numeric-input';
import { flushQueue, forgetSale, queueSale, queueStatus, recoverOutbox, type SaleQueueStatus } from '@/lib/offlineQueue';
import { draftHasItems, usePosDrafts, type CartLine, type PosDraft } from '@/lib/pos-drafts';
import { usePosUi } from '@/lib/pos-ui';
import { defaultReceiptConfig, loadEffectiveReceiptConfig, type PrintableReceipt } from '@/lib/receipt';
import { useSession } from '@/lib/session';
import { shelf } from '@/lib/shelf';

const card: CSSProperties = { background: colors.surface, border: `1px solid ${colors.border}`, borderRadius: 12, padding: spacing.lg };
const button: CSSProperties = { padding: `${spacing.sm} ${spacing.lg}`, borderRadius: 8, border: `1px solid ${colors.border}`, background: colors.surface, cursor: 'pointer', fontWeight: tokens.typography.weights.medium };

const emptyQueue: SaleQueueStatus = { pending: 0, retrying: 0, stuck: [], nextRetryAt: null };

/** Held-cart tab backgrounds, cycled so every open tab reads distinct at a glance. */
const heldTabColors = ['#fdeccb', '#d4e7fa', '#e4dcf9', '#d2f0de', '#f9d9e6', '#e9e7d4'];

export default function PosPage(): ReactNode {
  const { user } = useSession();
  const maySell = user !== null && can(user.role, 'sales.create');
  const mayReceive = user !== null && can(user.role, 'purchases.receive');
  const [mode, setMode] = useState<'sell' | 'receive'>(() => maySell ? 'sell' : 'receive');
  const [purchaseOrderId, setPurchaseOrderId] = useState<string | null>(null);
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const sourceOrderId = params.get('purchaseOrderId');
    if (params.get('mode') === 'receive' && mayReceive) setMode('receive');
    setPurchaseOrderId(sourceOrderId);
  }, [mayReceive]);
  useEffect(() => { if (!maySell && mayReceive) setMode('receive'); else if (!mayReceive && maySell) setMode('sell'); }, [mayReceive, maySell]);
  const modeSwitch = <CounterModeSwitch mode={mode} maySell={maySell} mayReceive={mayReceive} onMode={setMode} />;
  return <div className="pos-mode-shell">{mode === 'receive' ? <ReceiveWorkspace modeSwitch={modeSwitch} purchaseOrderId={purchaseOrderId} /> : <SellWorkspace modeSwitch={modeSwitch} />}</div>;
}

function CounterModeSwitch({ mode, maySell, mayReceive, onMode }: { mode: 'sell' | 'receive'; maySell: boolean; mayReceive: boolean; onMode: (mode: 'sell' | 'receive') => void }): ReactNode {
  return <div className="pos-mode-switch pos-mode-switch--footer" role="tablist" aria-label="Counter mode">
    {maySell && <button type="button" role="tab" aria-selected={mode === 'sell'} className={mode === 'sell' ? 'pos-mode-button pos-mode-button--active' : 'pos-mode-button'} onClick={() => onMode('sell')}><span>↑</span><strong>Sell</strong><small>To customers</small></button>}
    {mayReceive && <button type="button" role="tab" aria-selected={mode === 'receive'} className={mode === 'receive' ? 'pos-mode-button pos-mode-button--active' : 'pos-mode-button'} onClick={() => onMode('receive')}><span>↓</span><strong>Receive</strong><small>From suppliers</small></button>}
  </div>;
}

function SellWorkspace({ modeSwitch }: { modeSwitch: ReactNode }): ReactNode {
  const { user } = useSession();
  const queryClient = useQueryClient();
  const [stale, setStale] = useState<string | null>(null);
  const [query, setQuery] = useState('');
  /** The shelf row whose alternatives sub-list is open; one at a time. */
  const [altForId, setAltForId] = useState<string | null>(null);
  const draft = usePosDrafts((state) => state.active);
  const heldCarts = usePosDrafts((state) => state.held);
  const draftStatus = usePosDrafts((state) => state.status);
  const recoveryError = usePosDrafts((state) => state.recoveryError);
  const draftNotice = usePosDrafts((state) => state.notice);
  const hydrateDrafts = usePosDrafts((state) => state.hydrate);
  const updateActive = usePosDrafts((state) => state.updateActive);
  const holdActive = usePosDrafts((state) => state.holdActive);
  const resumeHeld = usePosDrafts((state) => state.resumeHeld);
  const deleteHeld = usePosDrafts((state) => state.deleteHeld);
  const clearActive = usePosDrafts((state) => state.clearActive);
  const reconcileActive = usePosDrafts((state) => state.reconcile);
  const resetCorruptStorage = usePosDrafts((state) => state.resetCorruptStorage);
  const flushDrafts = usePosDrafts((state) => state.flush);
  const cart = draft.lines;
  const customerId = draft.customerId;
  const customerName = draft.customerName;
  const globalDiscountMode = draft.globalDiscountMode;
  const globalDiscountValue = draft.globalDiscountValue;
  const deliveryCharge = draft.deliveryCharge;
  const otherFeeLabel = draft.otherFeeLabel;
  const otherFee = draft.otherFee;
  const advance = draft.advance;
  const advanceReference = draft.advanceReference;
  const setDraftField = useCallback(<K extends keyof PosDraft>(key: K, value: PosDraft[K]) => updateActive((current) => ({ ...current, [key]: value })), [updateActive]);
  const setCart = useCallback((change: CartLine[] | ((current: readonly CartLine[]) => CartLine[])) => updateActive((current) => ({ ...current, lines: typeof change === 'function' ? change(current.lines) : change })), [updateActive]);
  const cashReceived = usePosUi((state) => state.cashReceived);
  const digitalAmount = usePosUi((state) => state.digitalAmount);
  const digitalMethod = usePosUi((state) => state.digitalMethod);
  const redeemPoints = usePosUi((state) => state.redeemPoints);
  const receipt = usePosUi((state) => state.receipt);
  const setCashReceived = usePosUi((state) => state.setCashReceived);
  const setDigitalAmount = usePosUi((state) => state.setDigitalAmount);
  const setDigitalMethod = usePosUi((state) => state.setDigitalMethod);
  const setRedeemPoints = usePosUi((state) => state.setRedeemPoints);
  const setReceipt = usePosUi((state) => state.setReceipt);
  const resetTender = usePosUi((state) => state.resetTender);
  const [error, setError] = useState<string | null>(null);
  const [recovered, setRecovered] = useState(false);
  const [busy, setBusy] = useState(false);
  const [intakeSelection, setIntakeSelection] = useState<MedicineSelection | null>(null);
  const [approvalOpen, setApprovalOpen] = useState(false);
  const [approvalPhone, setApprovalPhone] = useState('');
  const [approvalPin, setApprovalPin] = useState('');
  const approvalPanelRef = useRef<HTMLDivElement>(null);
  const cashInputRef = useRef<HTMLInputElement>(null);
  const digitalInputRef = useRef<HTMLInputElement>(null);
  const heldRefs = useRef<(HTMLButtonElement | null)[]>([]);
  const [confirmAction, setConfirmAction] = useState<{ kind: 'clear' } | { kind: 'delete'; id: string; label: string } | null>(null);
  const [historyOpen, setHistoryOpen] = useState(false);

  useEffect(() => {
    if (draftStatus !== 'ready') return;
    const draftNeedsCleanup = draft.lines.some((line) => line.discountValue !== decimalEntry(line.discountValue))
      || globalDiscountValue !== decimalEntry(globalDiscountValue)
      || deliveryCharge !== decimalEntry(deliveryCharge)
      || otherFee !== decimalEntry(otherFee)
      || advance !== decimalEntry(advance);
    if (draftNeedsCleanup) {
      updateActive((current) => ({
        ...current,
        lines: current.lines.map((line) => ({ ...line, discountValue: decimalEntry(line.discountValue) })),
        globalDiscountValue: decimalEntry(current.globalDiscountValue),
        deliveryCharge: decimalEntry(current.deliveryCharge),
        otherFee: decimalEntry(current.otherFee),
        advance: decimalEntry(current.advance),
      }));
    }
    const safeCash = decimalEntry(cashReceived);
    const safeDigital = decimalEntry(digitalAmount);
    if (safeCash !== cashReceived) setCashReceived(safeCash);
    if (safeDigital !== digitalAmount) setDigitalAmount(safeDigital);
  }, [advance, cashReceived, deliveryCharge, digitalAmount, draft.lines, draftStatus, globalDiscountValue, otherFee, setCashReceived, setDigitalAmount, updateActive]);

  const storeId = user?.storeId ?? null;

  useEffect(() => {
    if (user?.organizationId && storeId) void hydrateDrafts(user.organizationId, storeId);
  }, [hydrateDrafts, storeId, user?.organizationId]);

  useEffect(() => {
    const onPageHide = (): void => { void flushDrafts(); };
    window.addEventListener('pagehide', onPageHide);
    return () => window.removeEventListener('pagehide', onPageHide);
  }, [flushDrafts]);
  const settingsQuery = useQuery({
    queryKey: ['organization', 'settings'],
    queryFn: async () => (await pharmacyApi.organizations.readSettings()).data.settings,
    staleTime: 60_000,
  });
  // The digital tenders this counter may book, exactly as the owner configured
  // them. Cash and due are structural and never come from this list.
  const digitalMethods = useMemo(
    () => (settingsQuery.data?.paymentMethods ?? []).filter((method) => method.active),
    [settingsQuery.data],
  );
  // Enroll-or-fetch: the endpoint answers with the existing account for a
  // customer already in the program, so attach alone is enough to show a balance.
  // A query that posts is unusual, but the endpoint is idempotent by design.
  const loyaltyAccount = useQuery({
    queryKey: ['pos', 'loyalty', customerId],
    enabled: customerId !== null,
    staleTime: 30_000,
    queryFn: async () => (await pharmacyApi.loyalty.enroll({ customerId: customerId as string })).data,
  });

  // A different customer's points must never ride along on the next sale.
  useEffect(() => { setRedeemPoints(''); }, [customerId, setRedeemPoints]);

  // The stored choice can fall out of the configured list (a method renamed  // inactive, settings just loaded); re-anchor it to the first real method.
  useEffect(() => {
    if (digitalMethods.length === 0) {
      if (digitalMethod !== '') setDigitalMethod('');
      if (digitalAmount !== '') setDigitalAmount('');
      return;
    }
    if (!digitalMethods.some((method) => method.value === digitalMethod)) {
      const first = digitalMethods[0];
      if (first !== undefined) setDigitalMethod(first.value);
    }
  }, [digitalAmount, digitalMethod, digitalMethods, setDigitalAmount, setDigitalMethod]);
  const receiptSettingsQuery = useQuery({
    queryKey: ['receipt-config', user?.organizationId, storeId],
    enabled: Boolean(user?.organizationId && storeId),
    staleTime: 60_000,
    queryFn: async () => {
      let locale = settingsQuery.data?.locale ?? 'en-BD';
      let timezone = settingsQuery.data?.defaultTimezone ?? 'Asia/Dhaka';
      let storeName = user?.storeName ?? '';
      const config = await loadEffectiveReceiptConfig(user!.organizationId, storeId as string, async () => {
        const [storeResponse, organizationResponse] = await Promise.all([
          pharmacyApi.stores.readCurrent(),
          pharmacyApi.organizations.readSettings(),
        ]);
        locale = organizationResponse.data.settings.locale;
        timezone = storeResponse.data.timezone;
        storeName = storeResponse.data.name;
        return { store: storeResponse.data.settings, organization: organizationResponse.data.settings };
      });
      return { config, locale, timezone, storeName };
    },
  });

  // The queue read is held back until `recoverOutbox` has settled -- see the mount
  // effect below. A status taken before recovery would show a sale stranded
  // mid-upload as either missing or permanently uploading.
  const queueQuery = useQuery({
    queryKey: ['pos', 'sale-queue'],
    queryFn: queueStatus,
    enabled: recovered,
    staleTime: 0,
  });
  const refetchQueue = useCallback(() => {
    void queryClient.invalidateQueries({ queryKey: ['pos', 'sale-queue'] });
  }, [queryClient]);

  useEffect(() => {
    // A sale left mid-upload by a closed tab is invisible to the flush until it is
    // put back in line, so this runs before anything reads the queue.
    void recoverOutbox().then(() => setRecovered(true), () => setRecovered(true));
  }, []);

  useEffect(() => {
    if (!approvalOpen) return;
    const previous = document.activeElement as HTMLElement | null;
    approvalPanelRef.current?.querySelector<HTMLInputElement>('input')?.focus();
    return () => previous?.focus();
  }, [approvalOpen]);

  const shelfLoad = useQuery({
    queryKey: ['pos', 'shelf', storeId],
    enabled: storeId !== null,
    // The shelf is the till's price list; a fresh fetch wins, but what is already
    // on disk answers immediately and survives a failed fetch (see `loadShelf`).
    staleTime: 60_000,
    queryFn: async (): Promise<ShelfLoad> =>
      loadShelf(shelf, storeId as string, async () => (await pharmacyApi.products.listCurrentStoreProducts()).items),
  });

  useEffect(() => {
    if (shelfLoad.data === undefined) return;
    setStale(shelfLoad.data.status === 'stale' ? shelfLoad.data.note : null);
  }, [shelfLoad.data]);

  const products: readonly ShelfProduct[] = useMemo(
    () => (shelfLoad.data !== undefined && shelfLoad.data.status !== 'unavailable' ? shelfLoad.data.products : []),
    [shelfLoad.data],
  );

  useEffect(() => {
    if (draftStatus === 'ready' && shelfLoad.data !== undefined && shelfLoad.data.status !== 'unavailable') reconcileActive(products);
  }, [draft.updatedAt, draftStatus, products, reconcileActive, shelfLoad.data]);

  const refetchShelf = useCallback(() => {
    if (storeId !== null) void queryClient.invalidateQueries({ queryKey: ['pos', 'shelf', storeId] });
  }, [queryClient, storeId]);

  const flush = useCallback(async () => {
    try {
      // Straight to `/sync/events`, which answers per event: a sale the server
      // cannot take yet is held and retried while the rest go through. This used
      // to replay each entry against `/sales`, which left device identity, client
      // sequences and per-event acks unexercised by the one client needing them.
      const result = await flushQueue(async (events) => (await pharmacyApi.sync.ingest(events)).data.acks);
      setError(result.firstError);
      if (result.uploaded + result.duplicates > 0) {
        refetchShelf();
        void queryClient.invalidateQueries({ queryKey: ['inventory'] });
        // Queued sales that carried cash change what the drawer should hold.
        void queryClient.invalidateQueries({ queryKey: ['pos', 'cash-session'] });
        void queryClient.invalidateQueries({ queryKey: ['pos', 'customer-history'] });
      }
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Upload failed');
    } finally {
      refetchQueue();
    }
  }, [queryClient, refetchQueue, refetchShelf]);

  useEffect(() => {
    const onOnline = (): void => {
      void flush();
      // The shelf too, not only the queue. A tab that spent the morning offline is
      // showing prices from before it lost the connection, and the cashier has no
      // reason to reload the page to find that out.
      refetchShelf();
    };
    window.addEventListener('online', onOnline);
    return () => window.removeEventListener('online', onOnline);
  }, [flush, refetchShelf]);

  const searching = query.trim() !== '';
  // One matcher for typing and for scanning, because on a desktop browser they are
  // the same event: a USB barcode gun types the digits into this box and presses
  // Enter. It used to be `sku.includes(needle)`, which found nothing for a scanned
  // barcode and nothing for a product name either. Grouping only while a query
  // is active: an untouched search box keeps the shell's plain shelf list.
  const view = useMemo(() => buildGroupedShelfView(products, query), [products, query]);
  const matches = view?.matches ?? [];
  const groups = view?.groups ?? [];
  const flatRows = view?.flatRows ?? [];
  const rowIndex = useMemo(() => new Map(flatRows.map((entry, index) => [entry, index])), [flatRows]);
  const rowRefs = useRef<(HTMLButtonElement | null)[]>([]);
  const searchRef = useRef<HTMLInputElement>(null);

  /** Arrow traversal walks medicine rows only, never the group headings. */
  function focusRow(index: number): void {
    const count = flatRows.length;
    if (count === 0) return;
    const next = ((index % count) + count) % count;
    rowRefs.current[next]?.focus();
  }

  const globalDiscount = globalDiscountValue.trim() === '' ? undefined : { mode: globalDiscountMode, value: globalDiscountValue } satisfies DiscountInput;
  const charges = useMemo<SaleChargeInput[]>(() => [
    ...(deliveryCharge.trim() === '' ? [] : [{ kind: 'delivery' as const, amount: deliveryCharge }]),
    ...(otherFee.trim() === '' ? [] : [{ kind: 'other' as const, amount: otherFee, ...(otherFeeLabel.trim() ? { label: otherFeeLabel.trim() } : {}) }]),
  ], [deliveryCharge, otherFee, otherFeeLabel]);
  const pricing = useMemo(() => {
    try {
      return { data: calculateCheckout(cart.map((line) => ({
        id: line.storeProductId, quantity: line.quantity, unitPrice: line.unitPrice,
        ...(line.discountValue.trim() === '' ? {} : { discount: { mode: line.discountMode, value: line.discountValue } }),
      })), globalDiscount, charges), problem: null as string | null };
    } catch (cause) {
      const fallback = calculateCheckout(cart.map((line) => ({ id: line.storeProductId, quantity: line.quantity, unitPrice: line.unitPrice })));
      return { data: fallback, problem: cause instanceof Error ? cause.message : 'Check the sale adjustments' };
    }
  }, [cart, globalDiscountMode, globalDiscountValue, charges]);
  const dueNow = useMemo(() => {
    try { return { amount: amountDueNow(pricing.data.total, advance), problem: null as string | null }; }
    catch (cause) { return { amount: pricing.data.total, problem: cause instanceof Error ? cause.message : 'Check the advance amount' }; }
  }, [pricing.data.total, advance]);

  const saleLines = useMemo(
    () =>
      cart.map((line, index) => ({
        id: line.storeProductId,
        productId: line.storeProductId,
        // The product name, so the slip reads "Paracetamol 500mg × 2" rather than
        // "PARA-500 × 2". The SKU was all the shelf endpoint returned before it was
        // joined to the product it sells.
        name: line.name,
        quantity: line.quantity,
        unitPrice: money(line.unitPrice),
        discount: money(pricing.data.lines[index]?.discountAmount ?? '0.00'),
        tax: money('0.00'),
      })),
    [cart, pricing.data.lines],
  );

  const totals = useMemo(() => ({
    subtotal: money(pricing.data.subtotal),
    discount: money(String((Number(pricing.data.lineDiscount) + Number(pricing.data.globalDiscount)).toFixed(2))),
    tax: money(String((Number(pricing.data.deliveryCharge) + Number(pricing.data.otherFee)).toFixed(2))),
    total: money(pricing.data.total), paid: money('0.00'), due: money('0.00'),
  }), [pricing.data]);

  const receiptHeader = { organizationName: user?.organizationName ?? '', storeName: receiptSettingsQuery.data?.storeName ?? user?.storeName ?? '', customerName };

  function printableReceipt(saleReceipt: SaleReceipt): PrintableReceipt {
    return {
      receipt: saleReceipt,
      config: receiptSettingsQuery.data?.config ?? defaultReceiptConfig,
      cashierName: user?.user.displayName ?? null,
      locale: receiptSettingsQuery.data?.locale ?? settingsQuery.data?.locale ?? 'en-BD',
      timezone: receiptSettingsQuery.data?.timezone ?? settingsQuery.data?.defaultTimezone ?? 'Asia/Dhaka',
    };
  }

  function addToCart(product: ShelfProduct): void {
    setCart((current) => {
      const existing = current.find((line) => line.storeProductId === product.id);
      if (existing) {
        return current.map((line) => (line.storeProductId === product.id ? { ...line, quantity: line.quantity + 1, rack: product.rack ?? line.rack } : line));
      }
      return [...current, { storeProductId: product.id, sku: product.sku, name: product.name, unit: product.unit ?? 'unit', quantity: 1, unitPrice: product.salePrice, discountMode: 'percentage', discountValue: '', rack: product.rack ?? null }];
    });
  }

  function selectMedicine(selection: MedicineSelection): void {
    if (selection.kind === 'local') {
      if (selection.item.availableQuantity === undefined || Number(selection.item.availableQuantity) > 0) addToCart(selection.item);
      else setIntakeSelection(selection);
      return;
    }
    if (selection.kind === 'catalog' && selection.item.shopStatus === 'on_shelf' && selection.item.storeProductId && selection.item.salePrice && Number(selection.item.availableQuantity ?? 0) > 0) {
      addToCart({
        id: selection.item.storeProductId, sku: selection.item.sku ?? '', name: selection.item.name,
        unit: selection.item.packageUnit ?? 'unit', salePrice: selection.item.salePrice,
        barcode: selection.item.barcode ?? null, rack: null,
      });
      return;
    }
    setIntakeSelection(selection);
  }

  function adoptIntoCart(intake: InventoryIntake): void {
    addToCart({
      id: intake.storeProductId, sku: intake.sku, name: intake.name, unit: intake.unit,
      salePrice: intake.salePrice, barcode: intake.barcode ?? null, rack: intake.rack ?? null,
      availableQuantity: intake.balance.available,
    });
    setIntakeSelection(null);
    refetchShelf();
    void queryClient.invalidateQueries({ queryKey: ['inventory'] });
    void queryClient.invalidateQueries({ queryKey: ['catalogue'] });
  }

  function setLineDiscount(storeProductId: string, change: Partial<Pick<CartLine, 'discountMode' | 'discountValue'>>): void {
    setCart((current) => current.map((line) => line.storeProductId === storeProductId ? { ...line, ...change } : line));
  }

  /**
   * Resolve a submitted search box against the cached shelf.
   *
   * This is the whole of scanner support in a browser. A USB gun is a keyboard: it
   * types the barcode and sends Enter. `submitShelfEntry` decides what is certain
   * enough to ring up -- a barcode or a SKU typed in full, never a partial name --
   * so the rule is the same here, on the till and behind the phone's camera.
   */
  function submitSearch(): void {
    const entry = submitShelfEntry(products, query);
    if (entry.status !== 'product') return;
    addToCart(entry.product);
    setQuery('');
    setError(null);
  }

  function setQuantity(storeProductId: string, raw: string): void {
    // Parsed and guarded here rather than at the input, because this value reaches
    // `calculateSaleTotals` during render and `multiply` throws on a quantity that
    // is not a whole number. Clearing the field yields `NaN`, so the old
    // `Number(...)` handler took the entire POS screen down and the cart with it --
    // the most expensive possible response to a cashier reaching for backspace.
    const trimmed = raw.trim();
    if (trimmed === '') return; // mid-edit; the last good quantity stays on screen
    const quantity = Number(trimmed);
    if (!Number.isInteger(quantity)) return;
    setCart((current) =>
      quantity <= 0
        ? current.filter((line) => line.storeProductId !== storeProductId)
        : current.map((line) => (line.storeProductId === storeProductId ? { ...line, quantity } : line)),
    );
  }

  function clearCart(): void {
    clearActive();
    resetTender();
  }

  // Loyalty redemption: what the entered points pay for and what they may not
  // exceed. The caps are advisory -- the server re-guards inside the sale's
  // transaction -- but refusing here keeps the cart on screen instead of a
  // rejected sale after the customer has left.
  const pointValue = Number(settingsQuery.data?.loyaltyPointValue ?? '0');
  const availablePoints = loyaltyAccount.data?.balance ?? 0;
  const redeemEntered = redeemPoints.trim() === '' ? 0 : Math.floor(Number(redeemPoints) || 0);
  const maxByDue = pointValue > 0 ? Math.floor(Number(dueNow.amount) / pointValue) : 0;
  const effectiveRedeem = Math.max(0, Math.min(redeemEntered, availablePoints, maxByDue));
  const loyaltyCredit = (effectiveRedeem * pointValue).toFixed(2);
  const collectNow = (Math.max(Number(dueNow.amount) - Number(loyaltyCredit), 0)).toFixed(2);

  const split = splitTender(collectNow, cashReceived, digitalAmount);

  async function checkout(approvalToken?: string): Promise<void> {
    if (cart.length === 0 || user === null) return;
    if (cart.some((line) => line.unavailable)) { setError('Remove unavailable items before checkout.'); return; }
    if (pricing.problem || dueNow.problem) { setError(pricing.problem ?? dueNow.problem); return; }
    if (Number(advance || 0) > 0 && customerId === null) { setError('Select a customer before applying an advance.'); return; }
    const hasStructuredDiscount = Number(pricing.data.lineDiscount) > 0 || Number(pricing.data.globalDiscount) > 0;
    if (hasStructuredDiscount && settingsQuery.data?.requirePinForDiscounts && approvalToken === undefined) {
      if (!navigator.onLine) { setError('Discount approval requires an internet connection. Remove the discount or reconnect.'); return; }
      setApprovalOpen(true);
      return;
    }
    if (!split.readable) {
      // Refusing beats guessing. The float parser this replaces turned an
      // unreadable field into 0.00 and carried on, so a mistyped digital amount
      // silently moved the whole sale onto cash or onto the customer's due balance.
      setError('Enter the tendered amounts as plain numbers, e.g. 250 or 250.50');
      return;
    }
    if (effectiveRedeem > 0 && !navigator.onLine) {
      // The server prices and deducts points inside the sale's transaction; an
      // offline queue entry cannot carry that guarantee, so it never tries.
      setError('Redeeming points needs a connection. Clear the redemption or reconnect to sell offline.');
      return;
    }
    setBusy(true);
    setError(null);
    // The tender rows, the "at least one payment" rule and the cash `receivedAmount`
    // all come from `@pharmacy/sales`, which holds them once for the three shells.
    const payments = tenderPayments(split, digitalMethod);
    try {
      // Checked before posting because every one of these is a refusal the server
      // makes after the fact -- by which point the cart is cleared and the customer
      // is walking away. `Due payments require a customer` is the one a counter hits.
      // The total here is what the tenders must cover: after advance and points.
      validateSalePayments(payments, money(collectNow), { hasCustomer: customerId !== null });
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'The tendered amounts do not add up');
      setBusy(false);
      return;
    }

    const body: SaleCreateRequest = {
      items: cart.map((line) => ({
        storeProductId: line.storeProductId,
        quantity: String(line.quantity),
        ...(line.discountValue.trim() === '' ? {} : { discount: { mode: line.discountMode, value: line.discountValue } }),
      })),
      payments: wirePayments(payments),
      ...(customerId === null ? {} : { customerId }),
      ...(globalDiscount === undefined ? {} : { globalDiscount }),
      ...(charges.length === 0 ? {} : { charges }),
      ...(advance.trim() === '' || Number(advance) === 0 ? {} : { advanceApplication: { amount: advance, ...(advanceReference.trim() ? { reference: advanceReference.trim() } : {}) } }),
      ...(effectiveRedeem > 0 ? { loyaltyRedemption: { points: effectiveRedeem } } : {}),
      ...(approvalToken === undefined ? {} : { discountApprovalToken: approvalToken }),
      subtotal: pricing.data.subtotal,
      total: pricing.data.total,
    };
    const idempotencyKey = newIdempotencyKey();

    try {
      if (navigator.onLine) {
        const response = await pharmacyApi.sales.create(body, { idempotencyKey });
        setReceipt(printableReceipt(receiptFromSale(response.data, receiptHeader)));
        clearCart();
        refetchShelf();
        void queryClient.invalidateQueries({ queryKey: ['inventory'] });
        void queryClient.invalidateQueries({ queryKey: ['pos', 'cash-session'] });
        void queryClient.invalidateQueries({ queryKey: ['pos', 'customer-history'] });
        if (customerId !== null) void earnLoyalty(response.data.id, response.data.total, customerId);
      } else {
        throw { code: 'NETWORK_ERROR' };
      }
    } catch (cause) {
      const offline = !navigator.onLine || (cause as { code?: string }).code === 'NETWORK_ERROR' || (cause as { name?: string }).name === 'TypeError';
      if (offline) {
        const context = envelopeContextFor(user);
        if (context === null) {
          // No device row means `/sync/events` would answer DEVICE_CONTEXT_REQUIRED,
          // so queueing would only hide the sale somewhere it can never leave.
          setError('Offline, and this terminal is not registered for offline sales yet. Sign in again once the server is reachable; keep the cart open and write this sale down.');
          return;
        }
        try {
          await queueSale(body, context);
          // The customer still gets a slip. It carries no receipt number, because
          // that number is the server's to assign and one invented here would later
          // belong to a different sale.
          setReceipt(printableReceipt(provisionalReceipt({
            ...receiptHeader, issuedAt: new Date().toISOString(), lines: saleLines, payments,
            total: money(pricing.data.total), deliveryCharge: money(pricing.data.deliveryCharge),
            otherFeeLabel: otherFeeLabel.trim() || null, otherFee: money(pricing.data.otherFee),
            advanceApplied: money(advance.trim() || '0.00'), advanceReference: advanceReference.trim() || null,
          })));
          setError('Offline — sale queued. It will upload automatically when the connection returns.');
          clearCart();
        } catch {
          // The queue write failed (no storage quota, or private browsing). The
          // cart is the only surviving record, so it stays and the message says so.
          setError('Offline and the sale could not be saved on this device. Keep the cart open and write the sale down before retrying.');
        }
      } else {
        // The cart deliberately survives a rejection. Nothing was recorded here or
        // on the server, so this cart is the only remaining evidence of what was
        // scanned -- clearing it left the cashier an error message and no way to
        // re-ring the sale or even say what it contained.
        setError(cause instanceof Error ? cause.message : 'Checkout failed');
      }
    } finally {
      refetchQueue();
      setBusy(false);
    }
  }

  /**
   * Post the sale's loyalty earn once the server has accepted it.
   *
   * The ledger row references the sale id, so earning cannot happen before the
   * sale exists — and an offline sale earns nothing here, because the id is the
   * server's to assign when the queued upload lands. The idempotency key is the
   * sale id, so a retried or double-fired earn is the same row, not double points.
   * A failure is said out loud but never unwinds the sale.
   */
  async function earnLoyalty(saleId: string, serverTotal: string, loyaltyCustomerId: string): Promise<void> {
    const rate = settingsQuery.data?.loyaltyPointsPerHundred ?? 0;
    if (rate <= 0) return;
    const points = Math.floor((Number(serverTotal) * rate) / 100);
    if (points <= 0) return;
    try {
      const account = loyaltyAccount.data ?? (await pharmacyApi.loyalty.enroll({ customerId: loyaltyCustomerId })).data;
      await pharmacyApi.loyalty.postTransaction(
        account.id,
        { transactionType: 'earn', points, sourceType: 'sale', sourceId: saleId },
        `loyalty-earn-${saleId}`,
      );
      void queryClient.invalidateQueries({ queryKey: ['pos', 'loyalty', loyaltyCustomerId] });
    } catch {
      setError(`Sale saved, but its ${points} loyalty points could not be added. Note it and adjust the account manually.`);
    }
  }

  async function approveAndCheckout(): Promise<void> {
    if (!approvalPhone.trim() || !approvalPin.trim()) { setError('Enter the approving manager phone and PIN.'); return; }
    setBusy(true); setError(null);
    try {
      const draft = {
        phone: approvalPhone.trim(), pin: approvalPin,
        items: cart.map((line) => ({ storeProductId: line.storeProductId, quantity: String(line.quantity), ...(line.discountValue.trim() ? { discount: { mode: line.discountMode, value: line.discountValue } } : {}) })),
        ...(globalDiscount === undefined ? {} : { globalDiscount }),
        ...(charges.length === 0 ? {} : { charges }),
      };
      const approval = await pharmacyApi.sales.approveDiscount(draft);
      setApprovalOpen(false); setApprovalPin('');
      await checkout(approval.data.token);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Discount approval failed');
    } finally { setBusy(false); }
  }

  const queue: SaleQueueStatus = queueQuery.data ?? emptyQueue;
  const queueProblem = queueQuery.isError && queueQuery.error instanceof Error ? queueQuery.error.message : null;
  const hasUnavailable = cart.some((line) => line.unavailable);
  const activeMethodLabel = digitalMethods.find((method) => method.value === digitalMethod)?.label ?? digitalMethod;

  function holdCurrentCart(): void {
    if (!holdActive()) { setError('Add an item before holding this cart.'); return; }
    resetTender();
    setError(null);
    document.querySelector<HTMLInputElement>('[aria-label="Search medicines"]')?.focus();
  }

  function resumeCart(id: string): void {
    if (!resumeHeld(id)) return;
    resetTender();
    setError(null);
  }

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent): void => {
      if (document.querySelector('[aria-modal="true"]')) return;
      const editable = event.target instanceof HTMLElement && event.target.matches('input, textarea, select, [contenteditable="true"]');
      if (event.key === '/' && !editable && !event.altKey && !event.ctrlKey && !event.metaKey) {
        event.preventDefault();
        document.querySelector<HTMLInputElement>('[aria-label="Search medicines"]')?.focus();
      } else if (event.key === 'F4') {
        event.preventDefault(); holdCurrentCart();
      } else if (event.key === 'F6') {
        event.preventDefault(); setHistoryOpen(true);
      } else if (event.key === 'F8') {
        event.preventDefault();
        if (heldRefs.current[0]) heldRefs.current[0].focus(); else setError('There are no held carts.');
      } else if (event.key === 'F9') {
        event.preventDefault(); cashInputRef.current?.focus();
      } else if (event.key === 'F10') {
        event.preventDefault(); digitalInputRef.current?.focus();
      } else if (event.key === 'F12') {
        event.preventDefault(); void checkout();
      }
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  });

  if (storeId === null) return <main className="page-shell"><p className="status-message status-message--error">Choose a branch before opening the counter.</p></main>;
  if (draftStatus === 'idle' || draftStatus === 'loading') return <main className="page-shell"><p className="status-message status-message--muted">Restoring carts from this terminal…</p></main>;
  if (draftStatus === 'corrupt') return <main className="page-shell"><section className="surface surface-section"><span className="eyebrow">Cart recovery</span><h1>Saved carts need attention</h1><p className="status-message status-message--error">{recoveryError}</p><button type="button" className="primary-action" style={{ marginTop: spacing.md }} onClick={() => void resetCorruptStorage()}>Reset local carts</button></section></main>;

  return (
    <>
    <main className="split-grid split-grid--counter">
      <section className="surface pos-shelf">
        <header className="pos-section-header">
          <div><span className="eyebrow">New sale</span><h1>Find medicine</h1></div>
          <span style={{ display: 'flex', alignItems: 'center', gap: spacing.xs }}>
            <span className="keyboard-hint">/ Search</span>
            <button type="button" className="quiet-action" onClick={() => setHistoryOpen(true)}>Returns / void <kbd>F6</kbd></button>
          </span>
        </header>
        {/* Said before the first click, not after the sale. A cashier quoting from a
            three-day-old price list should know that is what they are reading. */}
        {stale !== null && <p role="status" className="status-message status-message--warning">{stale}</p>}
        {shelfLoad.data?.status === 'unavailable' && (
          <p role="alert" className="status-message status-message--error">{shelfLoad.data.reason}</p>
        )}
        <MedicineFinder products={products} actionLabel="Add to sale" autoFocus onSelect={selectMedicine} />
        {products.length === 0 && <p className="empty-copy">Your shelf is empty. Search the global catalogue to add the first medicine while you sell it.</p>}
      </section>

      <aside className="pos-rail">
      <section className="surface pos-cart">
        <header className="cart-header">
          <div className="cart-title">
            <span className="cart-title-icon" aria-hidden="true"><PosIcon name="cart" /></span>
            <div><h2>Cart <span>({cart.length} {cart.length === 1 ? 'item' : 'items'})</span></h2><small>Saved automatically on this terminal</small></div>
          </div>
          <div className="cart-header-actions">
            {/* Off the Tab chain: F4 holds, and the chain must run search →
                quantity → discount without detouring through header buttons. */}
            <button type="button" tabIndex={-1} className="quiet-action" disabled={!draftHasItems(draft)} onClick={holdCurrentCart}><PosIcon name="pause" /> <span>Hold</span> <kbd>F4</kbd></button>
            <button type="button" tabIndex={-1} className="quiet-action danger-action" disabled={!draftHasItems(draft)} onClick={() => setConfirmAction({ kind: 'clear' })}><PosIcon name="trash" /> <span>Clear</span></button>
          </div>
        </header>
        {draftNotice && <p role="status" aria-live="polite" className="status-message cart-notice"><span aria-hidden="true"><PosIcon name="check" /></span>{draftNotice}</p>}
        <ul className="cart-lines">
          {cart.map((line, index) => (
            <li key={line.storeProductId} className={`cart-adjustment-row${line.unavailable ? ' cart-adjustment-row--unavailable' : ''}`}>
              <span className="line-number" aria-hidden="true">{index + 1}</span>
              <span className="line-details"><strong>{line.name}</strong><small className={line.unavailable ? 'line-unavailable' : ''}>{line.unavailable ? 'Unavailable — remove' : `${line.rack ? `Rack ${line.rack} · ` : ''}৳${line.unitPrice} / ${line.unit}`}</small></span>
              <div className="quantity-stepper" aria-label={`Quantity for ${line.name}`}>
                <button type="button" tabIndex={-1} aria-label={`Decrease ${line.name} quantity`} disabled={line.quantity <= 1} onClick={() => setQuantity(line.storeProductId, String(line.quantity - 1))}>−</button>
                <input
                  type="number"
                  min={1}
                  value={line.quantity}
                  onChange={(event) => setQuantity(line.storeProductId, event.target.value)}
                  onKeyDown={(event) => {
                    // Arrows step the quantity here instead of moving the caret, so
                    // the Tab chain is the only thing that leaves this field. The
                    // floor is 1: a stray ArrowDown must not drop the line.
                    if (event.key === 'ArrowUp') { event.preventDefault(); setQuantity(line.storeProductId, String(line.quantity + 1)); }
                    else if (event.key === 'ArrowDown' && line.quantity > 1) { event.preventDefault(); setQuantity(line.storeProductId, String(line.quantity - 1)); }
                  }}
                  aria-label={`Quantity for ${line.name}`}
                />
                <button type="button" tabIndex={-1} aria-label={`Increase ${line.name} quantity`} onClick={() => setQuantity(line.storeProductId, String(line.quantity + 1))}>+</button>
              </div>
              <div className="line-discount-control">
                <select tabIndex={-1} aria-label={`Discount type for ${line.name}`} value={line.discountMode} onChange={(event) => setLineDiscount(line.storeProductId, { discountMode: event.target.value as DiscountInput['mode'] })}>
                  <option value="percentage">% off</option><option value="flat">৳ off</option>
                </select>
                <input aria-label={`Discount for ${line.name}`} inputMode="decimal" placeholder="0" value={line.discountValue} onChange={(event) => setLineDiscount(line.storeProductId, { discountValue: decimalEntry(event.target.value) })} />
              </div>
              <strong className="line-total">৳{pricing.data.lines[index]?.net ?? lineTotal(line).amount}</strong>
              <button type="button" tabIndex={-1} className="line-remove" aria-label={`Remove ${line.name}`} onClick={() => setCart((current) => current.filter((entry) => entry.storeProductId !== line.storeProductId))}>×</button>
            </li>
          ))}
          {cart.length === 0 && <li className="cart-empty"><span aria-hidden="true">🛒</span><strong>No items in cart</strong><small>Search and add products to start a sale.</small></li>}
        </ul>

        <section className="cart-totals" aria-label="Sale totals">
          <div className="summary-row"><span>Subtotal</span><strong>৳{pricing.data.subtotal}</strong></div>
          {pricing.data.lineDiscount !== '0.00' && <div className="summary-row summary-row--discount"><span>Line discounts</span><strong>−৳{pricing.data.lineDiscount}</strong></div>}
          <div className="summary-row summary-row--editable">
            <label htmlFor="global-discount-mode">Discount</label>
            <div className="summary-discount-fields">
              <select tabIndex={-1} id="global-discount-mode" className="field" value={globalDiscountMode} onChange={(event) => setDraftField('globalDiscountMode', event.target.value as DiscountInput['mode'])}><option value="percentage">Percentage</option><option value="flat">Flat amount</option></select>
              <input className="field" aria-label="Global discount value" inputMode="decimal" placeholder="0" value={globalDiscountValue} onChange={(event) => setDraftField('globalDiscountValue', decimalEntry(event.target.value))} />
            </div>
            <strong className="discount-value">−৳{pricing.data.globalDiscount}</strong>
          </div>
          <div className="summary-charges">
            <label><span>Delivery charge</span><span className="money-input"><span>৳</span><input tabIndex={-1} aria-label="Delivery charge" inputMode="decimal" placeholder="0.00" value={deliveryCharge} onChange={(event) => setDraftField('deliveryCharge', decimalEntry(event.target.value))} /></span></label>
            <label><span>Other fee</span><span className="money-input"><span>৳</span><input tabIndex={-1} aria-label="Other fee" inputMode="decimal" placeholder="0.00" value={otherFee} onChange={(event) => setDraftField('otherFee', decimalEntry(event.target.value))} /></span></label>
          </div>
          {otherFee.trim() !== '' && Number(otherFee) > 0 && <input tabIndex={-1} className="field other-fee-label" aria-label="Other fee label" placeholder="Describe other fee" value={otherFeeLabel} onChange={(event) => setDraftField('otherFeeLabel', event.target.value)} />}
          <div className="summary-total"><span>Total</span><strong>৳{pricing.data.total}</strong></div>
        </section>
        {pricing.problem && <p role="alert" className="form-error" style={{ margin: 0 }}>{pricing.problem}</p>}

        <CustomerCombobox
          selectedId={customerId}
          selectedLabel={customerName}
          loyaltyPoints={loyaltyAccount.data?.balance ?? null}
          onSelect={(pick) => {
            updateActive((current) => ({ ...current, customerId: pick.id, customerName: pick.label }));
            setError(null);
          }}
          onClear={() => updateActive((current) => ({ ...current, customerId: null, customerName: null }))}
          onError={setError}
        />

        {customerId !== null && <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: spacing.xs }}>
          <label style={{ fontSize: tokens.typography.sizes.sm }}>Advance applied<input tabIndex={-1} className="field" inputMode="decimal" placeholder="0.00" value={advance} onChange={(event) => setDraftField('advance', decimalEntry(event.target.value))} /></label>
          <label style={{ fontSize: tokens.typography.sizes.sm }}>Advance reference<input tabIndex={-1} className="field" placeholder="Order or receipt" value={advanceReference} onChange={(event) => setDraftField('advanceReference', event.target.value)} /></label>
        </div>}
        {customerId !== null && availablePoints > 0 && pointValue > 0 && (
          <label style={{ fontSize: tokens.typography.sizes.sm, display: 'flex', flexDirection: 'column', gap: spacing.xs }}>
            Redeem points (of {availablePoints} · ৳{pointValue.toFixed(2)}/pt)
            <input
              tabIndex={-1}
              className="field"
              inputMode="numeric"
              placeholder="0"
              aria-label="Points to redeem"
              value={redeemPoints}
              onChange={(event) => setRedeemPoints(event.target.value.replace(/[^\d]/g, ''))}
            />
            {effectiveRedeem > 0 && (
              <small style={{ color: colors.muted }}>
                Pays ৳{loyaltyCredit} of this sale; ৳{collectNow} left to collect.
              </small>
            )}
          </label>
        )}
        {(advance.trim() !== '' && Number(advance) > 0) || effectiveRedeem > 0 ? <TotalRow label="Amount to collect now" value={`৳${collectNow}`} /> : null}
        {dueNow.problem && <p role="alert" className="form-error" style={{ margin: 0 }}>{dueNow.problem}</p>}

        <section className="payment-section" aria-label="Payment">
          <label className="payment-field">
            <span>Cash received</span>
            <span className="payment-input"><PosIcon name="cash" /><input
              ref={cashInputRef}
              value={cashReceived}
              onChange={(event) => setCashReceived(decimalEntry(event.target.value))}
              onKeyDown={(event) => {
                // The Tab chain ends here, so Enter is the whole sale: the amount
                // is already committed on each keystroke, nothing is pending.
                if (event.key === 'Enter' && !busy) { event.preventDefault(); void checkout(); }
              }}
              placeholder={dueNow.amount}
              inputMode="decimal"
            /></span>
          </label>
          {digitalMethods.length > 0 && (
            <label className="payment-field">
              <span>Digital ({activeMethodLabel}) amount</span>
              <span className="payment-input"><PosIcon name="phone" /><input ref={digitalInputRef} value={digitalAmount} onChange={(event) => setDigitalAmount(decimalEntry(event.target.value))} placeholder="0.00" inputMode="decimal" /></span>
            </label>
          )}
        </section>
        {digitalMethods.length > 0 && (
          <div className="payment-methods" aria-label="Digital payment method">
            {digitalMethods.map((method) => (
              <button key={method.value} type="button" className={digitalMethod === method.value ? 'payment-method payment-method--active' : 'payment-method'} aria-pressed={digitalMethod === method.value} onClick={() => setDigitalMethod(method.value)}>
                <span className={`payment-mark${method.value === 'nagad' ? ' payment-mark--nagad' : ''}`} aria-hidden="true">{method.value === 'bkash' ? '➤' : '●'}</span>{method.label}
              </button>
            ))}
          </div>
        )}

        <div className="tender-summary">
          <span><PosIcon name="wallet" /> Cash ৳{split.cash} {activeMethodLabel !== '' && <><i>·</i> {activeMethodLabel} ৳{split.digital}</>}</span>
          <strong className={split.due === '0.00' ? '' : 'has-due'}>Due ৳{split.due}</strong>
        </div>
        {!split.readable && (
          <p role="alert" style={{ margin: 0, color: colors.danger, fontSize: tokens.typography.sizes.sm }}>
            One of the tendered amounts is not a number. Charging is blocked until it is corrected.
          </p>
        )}
        {/* Change is displayed, not inferred: the cashier has to know what to hand
            back, and the server records `receivedAmount` so the drawer reconciles. */}
        {split.change !== '0.00' && (
          <div className="change-due">
            <span>Change due</span>
            <strong>৳{split.change}</strong>
          </div>
        )}

        <button type="button" className="primary-action complete-sale" disabled={busy || cart.length === 0 || hasUnavailable || pricing.problem !== null || dueNow.problem !== null} onClick={() => void checkout()}>
          <span>{busy ? 'Charging…' : 'Complete sale'}</span><kbd>F12</kbd>
        </button>

        {queueProblem !== null && <p role="alert" style={{ margin: 0, color: colors.danger }}>{queueProblem}</p>}
        {queue.pending > 0 && (
          <button type="button" style={{ ...button, borderColor: colors.warning }} onClick={() => void flush()}>
            {queue.pending} offline sale(s) queued — upload now
            {queue.nextRetryAt !== null ? ` (next retry ${new Date(queue.nextRetryAt).toLocaleTimeString()})` : ''}
          </button>
        )}
        {queue.stuck.length > 0 && (
          <StuckSales
            entries={queue.stuck}
            onSettle={(eventId) => {
              void forgetSale(eventId).then(refetchQueue);
            }}
          />
        )}
        {error !== null && <p role="alert" style={{ margin: 0, color: error.startsWith('Offline') ? colors.warning : colors.danger }}>{error}</p>}
        {receipt !== null && <ReceiptDialog printable={receipt} onClose={() => setReceipt(null)} />}
      </section>
      </aside>
    </main>
    <nav className="held-tabstrip" aria-label="Held carts">
      <div className="held-strip-label">
        <PosIcon name="pause" />
        <strong>Held</strong>
        <span className="held-strip-count">{heldCarts.length}</span>
        <kbd>F8</kbd>
      </div>
      {heldCarts.length === 0 ? <p className="held-strip-empty">Held carts stack here as tabs — hold the current sale with <kbd>F4</kbd>.</p> : <ul className="held-tabs">
        {heldCarts.map((held, index) => {
          const quantity = held.draft.lines.reduce((sum, line) => sum + line.quantity, 0);
          const total = held.draft.lines.reduce((sum, line) => sum + Number(line.unitPrice) * line.quantity, 0).toFixed(2);
          return <li key={held.id} className="held-tab" style={{ background: heldTabColors[index % heldTabColors.length] }} onClick={() => resumeCart(held.id)}>
            <button ref={(node) => { heldRefs.current[index] = node; }} type="button" className="held-main" onKeyDown={(event) => {
              if (event.key === 'ArrowDown') { event.preventDefault(); heldRefs.current[(index + 1) % heldCarts.length]?.focus(); }
              else if (event.key === 'ArrowUp') { event.preventDefault(); heldRefs.current[(index - 1 + heldCarts.length) % heldCarts.length]?.focus(); }
            }}>
              <span><strong>{held.label}</strong><small>{quantity} item{quantity === 1 ? '' : 's'} · ৳{total}</small></span>
            </button>
            <button type="button" className="line-remove" aria-label={`Delete held cart ${held.label}`} onClick={(event) => { event.stopPropagation(); setConfirmAction({ kind: 'delete', id: held.id, label: held.label }); }}>×</button>
            {/* Hover preview, like a browser tab card: what is inside before resuming it. */}
            <div className="held-tab-preview" aria-hidden="true">
              <strong>{held.label}</strong>
              <ul>
                {held.draft.lines.map((line) => <li key={line.storeProductId}><span>{line.name}</span><span>×{line.quantity}</span></li>)}
              </ul>
              <small>Held {new Date(held.heldAt).toLocaleTimeString()}</small>
            </div>
          </li>;
        })}
      </ul>}
      {modeSwitch}
      <ShiftPanel onError={setError} />
    </nav>
    {confirmAction && <ConfirmDialog
      title={confirmAction.kind === 'clear' ? 'Clear active cart?' : 'Remove held cart?'}
      message={confirmAction.kind === 'clear' ? 'This removes the active sale draft from this terminal. Held carts are not affected.' : `“${confirmAction.label}” will be removed from this terminal.`}
      onCancel={() => setConfirmAction(null)}
      onConfirm={() => { if (confirmAction.kind === 'clear') clearCart(); else deleteHeld(confirmAction.id); setConfirmAction(null); }}
    />}
    {intakeSelection && <IntakeDrawer selection={intakeSelection} source="opening_stock" onClose={() => setIntakeSelection(null)} onSaved={adoptIntoCart} />}
    {approvalOpen && <div className="drawer-backdrop" onMouseDown={(event) => { if (event.target === event.currentTarget) setApprovalOpen(false); }}>
      <div
        ref={approvalPanelRef}
        className="intake-drawer"
        role="dialog"
        aria-modal="true"
        aria-labelledby="approval-title"
        style={{ width: 'min(420px, 100%)' }}
        onKeyDown={(event) => {
          if (event.key === 'Escape') { event.preventDefault(); setApprovalOpen(false); return; }
          if (event.key !== 'Tab') return;
          const focusable = Array.from(approvalPanelRef.current?.querySelectorAll<HTMLElement>('button:not([disabled]), input:not([disabled]), [tabindex]:not([tabindex="-1"])') ?? []);
          const first = focusable[0];
          const last = focusable.at(-1);
          if (event.shiftKey && document.activeElement === first && last) { event.preventDefault(); last.focus(); }
          else if (!event.shiftKey && document.activeElement === last && first) { event.preventDefault(); first.focus(); }
        }}
      >
        <header><div><span className="eyebrow">Protected discount</span><h2 id="approval-title">Manager approval</h2><p>The approval expires in five minutes and is tied to this cart.</p></div><button type="button" className="quiet-action" onClick={() => setApprovalOpen(false)}>Close</button></header>
        <div className="drawer-fields">
          <label>Owner or manager phone<input className="field" autoFocus value={approvalPhone} onChange={(event) => setApprovalPhone(event.target.value)} /></label>
          <label>PIN<input className="field" type="password" inputMode="numeric" value={approvalPin} onChange={(event) => setApprovalPin(event.target.value)} onKeyDown={(event) => { if (event.key === 'Enter') void approveAndCheckout(); }} /></label>
          {error && <p role="alert" className="form-error">{error}</p>}
        </div>
        <footer><button type="button" className="quiet-action" onClick={() => setApprovalOpen(false)}>Cancel</button><button type="button" className="primary-action" disabled={busy} onClick={() => void approveAndCheckout()}>{busy ? 'Checking…' : 'Approve and complete sale'}</button></footer>
      </div>
    </div>}
    {historyOpen && user !== null && (
      <SalesHistoryDialog
        role={user.role}
        onClose={() => setHistoryOpen(false)}
        onStockChanged={() => {
          refetchShelf();
          void queryClient.invalidateQueries({ queryKey: ['inventory'] });
          void queryClient.invalidateQueries({ queryKey: ['pos', 'cash-session'] });
          void queryClient.invalidateQueries({ queryKey: ['pos', 'customer-history'] });
        }}
      />
    )}
    </>
  );
}

function PosIcon({ name }: { name: 'cart' | 'pause' | 'trash' | 'check' | 'user' | 'search' | 'cash' | 'phone' | 'wallet' }): ReactNode {
  const common = { className: 'pos-icon', viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', strokeLinecap: 'round' as const, strokeLinejoin: 'round' as const };
  switch (name) {
    case 'cart': return <svg {...common}><path d="M3 4h2l1.6 10.2a2 2 0 0 0 2 1.7h8.7a2 2 0 0 0 1.9-1.5L21 7H6" /><circle cx="9" cy="20" r="1" /><circle cx="18" cy="20" r="1" /></svg>;
    case 'pause': return <svg {...common}><path d="M8 5v14M16 5v14" /></svg>;
    case 'trash': return <svg {...common}><path d="M4 7h16M9 7V4h6v3M7 7l1 13h8l1-13M10 11v5M14 11v5" /></svg>;
    case 'check': return <svg {...common}><path d="m6.5 12.5 3.3 3.3 7.7-8" /></svg>;
    case 'user': return <svg {...common}><circle cx="12" cy="8" r="3.5" /><path d="M5 20a7 7 0 0 1 14 0" /></svg>;
    case 'search': return <svg {...common}><circle cx="10.5" cy="10.5" r="5.5" /><path d="m15 15 4 4" /></svg>;
    case 'cash': return <svg {...common}><rect x="3" y="6" width="18" height="12" rx="2" /><circle cx="12" cy="12" r="2.5" /><path d="M6 9h.01M18 15h.01" /></svg>;
    case 'phone': return <svg {...common}><rect x="7" y="2.5" width="10" height="19" rx="2" /><path d="M10 5h4M11 18.5h2" /></svg>;
    case 'wallet': return <svg {...common}><path d="M4 7.5V6a2 2 0 0 1 2-2h12v4M4 7.5h15a2 2 0 0 1 2 2V18a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2Z" /><path d="M16 13h5" /></svg>;
  }
}

function TotalRow({ label, value }: { label: string; value: string }): ReactNode {
  return <div style={{ display: 'flex', justifyContent: 'space-between', color: colors.muted, fontSize: tokens.typography.sizes.sm }}><span>{label}</span><span>{value}</span></div>;
}

function ConfirmDialog({ title, message, onCancel, onConfirm }: { title: string; message: string; onCancel: () => void; onConfirm: () => void }): ReactNode {
  return <div className="dialog-backdrop" onMouseDown={(event) => { if (event.target === event.currentTarget) onCancel(); }}>
    <section className="dialog-panel" role="dialog" aria-modal="true" aria-labelledby="confirm-title" onKeyDown={(event) => { if (event.key === 'Escape') onCancel(); }}>
      <header className="dialog-header"><div><span className="eyebrow">Please confirm</span><h2 id="confirm-title">{title}</h2></div></header>
      <p style={{ color: colors.muted, lineHeight: 1.5 }}>{message}</p>
      <footer style={{ display: 'flex', justifyContent: 'flex-end', gap: spacing.sm }}><button type="button" className="quiet-action" onClick={onCancel}>Cancel</button><button autoFocus type="button" className="primary-action" onClick={onConfirm}>Confirm</button></footer>
    </section>
  </div>;
}

/**
 * Queued sales the server will not accept, listed so somebody can settle them by hand.
 *
 * These are not retried and they do not disappear: each one is money already taken
 * over the counter for stock that already left the shelf, and the queued payload is
 * the only surviving record of it. Clearing one is therefore a deliberate act by a
 * person who has re-entered the sale, never a side effect of a failed upload.
 */
function StuckSales({ entries, onSettle }: { entries: readonly StuckEntry<SaleCreateRequest>[]; onSettle: (eventId: string) => void }): ReactNode {
  return (
    <div style={{ border: `1px solid ${colors.danger}`, borderRadius: 8, padding: spacing.sm, display: 'flex', flexDirection: 'column', gap: spacing.xs }}>
      <strong style={{ color: colors.danger }}>{entries.length} sale(s) the server will not accept</strong>
      <span style={{ fontSize: tokens.typography.sizes.sm }}>Re-enter each one, then clear it.</span>
      {entries.map((entry) => (
        <div key={entry.eventId} style={{ display: 'flex', alignItems: 'baseline', gap: spacing.sm, fontSize: tokens.typography.sizes.sm }}>
          <span style={{ flex: 1 }}>
            ৳{entry.payload.total ?? '?'} · {entry.payload.items.length} line(s) · {new Date(entry.createdAt).toLocaleString()}
            <br />
            <span style={{ color: colors.muted }}>{entry.reason ?? 'Rejected'}</span>
          </span>
          <button type="button" style={{ ...button, padding: `${spacing.xs} ${spacing.sm}` }} onClick={() => onSettle(entry.eventId)}>
            Re-entered — clear
          </button>
        </div>
      ))}
    </div>
  );
}

function lineTotal(line: CartLine): MoneyValue {
  return multiply(money(line.unitPrice), line.quantity);
}

/**
 * One medicine row inside the grouped results.
 *
 * Brand first, then generic/strength/form/SKU/rack, price on the right: the
 * identity hierarchy a counter scans down. The match label is shown for
 * everything that is not an exact brand/barcode/SKU hit, because "Closest brand
 * match" is the difference between a confirmed row and a guess the cashier is
 * about to act on. Enter on a focused row selects it; the search box's Enter
 * keeps its scan-safe `submitShelfEntry` behavior instead.
 */
function ShelfRow({
  entry,
  index,
  query,
  altAvailable,
  altOpen,
  onToggleAlt,
  onAdd,
  onFocusRow,
  onEscapeToInput,
  registerRef,
}: {
  entry: RankedMedicine<ShelfProduct>;
  index: number;
  query: string;
  altAvailable: boolean;
  altOpen: boolean;
  onToggleAlt: () => void;
  onAdd: () => void;
  onFocusRow: (index: number) => void;
  onEscapeToInput: () => void;
  registerRef: (node: HTMLButtonElement | null) => void;
}): ReactNode {
  const product = entry.item;
  const meta = [product.genericName, product.strength, product.dosageForm, product.sku, product.rack]
    .filter((part): part is string => Boolean(part))
    .join(' · ');
  const labelled = !(entry.matchQuality === 'exact' && entry.matchedField === 'name');
  return (
    <div style={{ display: 'flex', gap: spacing.xs, alignItems: 'stretch' }}>
      <button
        type="button"
        ref={registerRef}
        onClick={onAdd}
        onKeyDown={(event) => {
          if (event.key === 'ArrowDown') {
            event.preventDefault();
            onFocusRow(index + 1);
          } else if (event.key === 'ArrowUp') {
            event.preventDefault();
            if (index === 0) onEscapeToInput();
            else onFocusRow(index - 1);
          } else if (event.key === 'a' && !event.altKey && !event.ctrlKey && !event.metaKey && altAvailable) {
            // "What else is there" is one keystroke from the row it is about.
            event.preventDefault();
            onToggleAlt();
          } else if (event.key === 'Escape') {
            if (altOpen) onToggleAlt();
            else onEscapeToInput();
          }
        }}
        style={{ ...button, flex: 1, minWidth: 0, textAlign: 'left', display: 'flex', justifyContent: 'space-between', gap: spacing.sm }}
      >
        <span style={{ display: 'flex', flexDirection: 'column', minWidth: 0 }}>
          <span>
            {highlightMedicineSpans(product.name, query).map((span, position) =>
              span.hit ? (
                <mark key={position} style={{ background: 'transparent', color: colors.foreground, fontWeight: tokens.typography.weights.semibold, textDecoration: 'underline' }}>
                  {span.text}
                </mark>
              ) : (
                <span key={position}>{span.text}</span>
              ),
            )}
          </span>
          <span style={{ color: colors.muted, fontSize: tokens.typography.sizes.sm }}>
            {meta}
            {labelled && (
              <span style={{ marginLeft: spacing.sm, color: colors.warning }}>· {describeMedicineMatch(entry)}</span>
            )}
          </span>
        </span>
        <span style={{ color: colors.muted }}>৳{product.salePrice}</span>
      </button>
      {altAvailable && (
        <button
          type="button"
          aria-label={`Alternatives to ${product.name}`}
          aria-expanded={altOpen}
          onClick={onToggleAlt}
          style={{ ...button, padding: `${spacing.xs} ${spacing.sm}`, fontSize: tokens.typography.sizes.sm }}
        >
          Alt
        </button>
      )}
    </div>
  );
}

/**
 * The alternatives sub-list under a row: this shelf's brands of the same generic
 * first (tap to ring one up), then brands the shared catalogue carries that this
 * branch does not stock, read-only -- the counter sells, it does not adopt.
 *
 * The shelf section is local computation and always answers, offline included.
 * The catalogue section is best-effort: a fetch that fails renders nothing,
 * because "no other brands known" and "cannot ask right now" are the same dead
 * end to a cashier mid-sale, and the shelf answer is still on screen.
 */
function AlternativeList({
  products,
  target,
  onAdd,
  onClose,
}: {
  products: readonly ShelfProduct[];
  target: ShelfProduct;
  onAdd: (item: ShelfProduct) => void;
  onClose: () => void;
}): ReactNode {
  const generic = target.genericName ?? '';
  const shelfAlternatives = useMemo(() => findMedicineAlternatives(products, target), [products, target]);
  const catalogueQuery = useQuery({
    queryKey: ['pos', 'alternatives', generic, target.strength ?? ''],
    queryFn: async () =>
      await pharmacyApi.products.alternatives(
        {
          genericName: generic,
          ...(target.strength ? { strength: target.strength } : {}),
          ...(target.dosageFormId ? { dosageFormId: target.dosageFormId } : {}),
        },
        { limit: 20 },
      ),
    enabled: generic.trim() !== '',
    retry: false,
    staleTime: 30_000,
  });
  const catalogueItems = catalogueQuery.data?.items ?? [];
  const otherBrands = useMemo(
    () => mergeMedicineAlternatives([target, ...shelfAlternatives.map((alt) => alt.item)], catalogueItems),
    [shelfAlternatives, catalogueItems, target],
  );

  return (
    <div style={{ marginLeft: spacing.md, paddingLeft: spacing.sm, borderLeft: `2px solid ${colors.border}`, display: 'flex', flexDirection: 'column', gap: spacing.xs }}>
      <strong style={{ fontSize: tokens.typography.sizes.sm }}>Alternatives to {target.name}</strong>
      <span style={{ fontSize: tokens.typography.sizes.sm, color: colors.muted }}>Same generic: {generic}</span>
      {shelfAlternatives.length === 0 ? (
        <span style={{ fontSize: tokens.typography.sizes.sm, color: colors.muted }}>
          No other brand of this generic on this shelf.
        </span>
      ) : (
        shelfAlternatives.map((alt) => (
          <button
            key={alt.item.id}
            type="button"
            onClick={() => onAdd(alt.item)}
            style={{ ...button, textAlign: 'left', display: 'flex', justifyContent: 'space-between', gap: spacing.sm }}
          >
            <span>
              {alt.item.name}
              <span style={{ color: colors.muted, fontSize: tokens.typography.sizes.sm }}>
                {' '}{[alt.item.strength, alt.item.manufacturer].filter((part): part is string => Boolean(part)).join(' · ')}
              </span>
            </span>
            <span style={{ color: alt.sameStrength ? colors.muted : colors.warning, fontSize: tokens.typography.sizes.sm, whiteSpace: 'nowrap' }}>
              {alt.sameStrength ? 'same strength' : `different strength${alt.item.strength ? ` (${alt.item.strength})` : ''}`} · ৳{alt.item.salePrice}
            </span>
          </button>
        ))
      )}
      {otherBrands.length > 0 && (
        <>
          <span style={{ fontSize: tokens.typography.sizes.sm, color: colors.muted, fontWeight: tokens.typography.weights.semibold }}>
            Other brands this branch does not stock
          </span>
          {otherBrands.map((brand) => (
            <div key={brand.catalogProductId} style={{ display: 'flex', justifyContent: 'space-between', gap: spacing.sm, fontSize: tokens.typography.sizes.sm, color: colors.muted }}>
              <span>
                {brand.name}
                {[brand.strength, brand.manufacturer].filter((part): part is string => Boolean(part)).length > 0
                  ? ` · ${[brand.strength, brand.manufacturer].filter((part): part is string => Boolean(part)).join(' · ')}`
                  : ''}
                {!brand.sameStrength && ' (different strength)'}
              </span>
              <span>{brand.referenceUnitPrice !== null && brand.referenceUnitPrice !== undefined ? `ref ৳${brand.referenceUnitPrice}` : ''}</span>
            </div>
          ))}
        </>
      )}
      <button type="button" style={{ ...button, alignSelf: 'flex-start', padding: `${spacing.xs} ${spacing.sm}` }} onClick={onClose}>
        Close
      </button>
    </div>
  );
}

function newIdempotencyKey(): string {
  if (typeof crypto.randomUUID === 'function') return crypto.randomUUID();
  // `randomUUID` is secure-context only, so a counter served over plain HTTP on
  // the shop LAN takes this path. `Math.random()` was the old fallback: around
  // 40 bits and predictable, for the one value standing between a retried upload
  // and a double-booked sale. `getRandomValues` carries no such restriction.
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  return `web-${Array.from(bytes, (byte) => byte.toString(16).padStart(2, '0')).join('')}`;
}

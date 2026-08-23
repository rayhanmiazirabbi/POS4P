'use client';

import { useQuery, useQueryClient } from '@tanstack/react-query';
import type { SaleCreateRequest } from '@pharmacy/api';
import {
  calculateSaleTotals,
  formatReceiptText,
  provisionalReceipt,
  receiptFromSale,
  splitTender,
  tenderPayments,
  validateSalePayments,
  wirePayments,
  type Receipt as ReceiptModel,
} from '@pharmacy/sales';
import { money, multiply, type MoneyValue } from '@pharmacy/money';
import { colors, spacing, tokens } from '@pharmacy/design-tokens';
import { envelopeContextFor, loadShelf, matchShelf, submitShelfEntry, type ShelfLoad, type ShelfProduct, type StuckEntry } from '@pharmacy/sync';
import { useCallback, useEffect, useMemo, useState, type CSSProperties, type ReactNode } from 'react';

import { pharmacyApi } from '@/lib/api';
import { flushQueue, forgetSale, queueSale, queueStatus, recoverOutbox, type SaleQueueStatus } from '@/lib/offlineQueue';
import { usePosUi } from '@/lib/pos-ui';
import { useSession } from '@/lib/session';
import { shelf } from '@/lib/shelf';

type CartLine = { storeProductId: string; sku: string; name: string; quantity: number; unitPrice: string };

const card: CSSProperties = { background: colors.surface, border: `1px solid ${colors.border}`, borderRadius: 12, padding: spacing.lg };
const button: CSSProperties = { padding: `${spacing.sm} ${spacing.lg}`, borderRadius: 8, border: `1px solid ${colors.border}`, background: colors.surface, cursor: 'pointer', fontWeight: tokens.typography.weights.medium };

const emptyQueue: SaleQueueStatus = { pending: 0, retrying: 0, stuck: [], nextRetryAt: null };

export default function PosPage(): ReactNode {
  const { user } = useSession();
  const queryClient = useQueryClient();
  const [stale, setStale] = useState<string | null>(null);
  const [query, setQuery] = useState('');
  const [cart, setCart] = useState<CartLine[]>([]);
  const [customerId, setCustomerId] = useState<string | null>(null);
  const [customerName, setCustomerName] = useState<string | null>(null);
  const cashReceived = usePosUi((state) => state.cashReceived);
  const digitalAmount = usePosUi((state) => state.digitalAmount);
  const digitalMethod = usePosUi((state) => state.digitalMethod);
  const receipt = usePosUi((state) => state.receipt);
  const setCashReceived = usePosUi((state) => state.setCashReceived);
  const setDigitalAmount = usePosUi((state) => state.setDigitalAmount);
  const setDigitalMethod = usePosUi((state) => state.setDigitalMethod);
  const setReceipt = usePosUi((state) => state.setReceipt);
  const resetTender = usePosUi((state) => state.resetTender);
  const [error, setError] = useState<string | null>(null);
  const [recovered, setRecovered] = useState(false);
  const [busy, setBusy] = useState(false);

  const storeId = user?.storeId ?? null;

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
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Upload failed');
    } finally {
      refetchQueue();
    }
  }, [refetchQueue]);

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

  const filtered = useMemo(() => {
    // One matcher for typing and for scanning, because on a desktop browser they are
    // the same event: a USB barcode gun types the digits into this box and presses
    // Enter. It used to be `sku.includes(needle)`, which found nothing for a scanned
    // barcode and nothing for a product name either.
    if (query.trim() === '') return products;
    return matchShelf(products, query).map((match) => match.product);
  }, [products, query]);

  const saleLines = useMemo(
    () =>
      cart.map((line) => ({
        id: line.storeProductId,
        productId: line.storeProductId,
        // The product name, so the slip reads "Paracetamol 500mg × 2" rather than
        // "PARA-500 × 2". The SKU was all the shelf endpoint returned before it was
        // joined to the product it sells.
        name: line.name,
        quantity: line.quantity,
        unitPrice: money(line.unitPrice),
        discount: money('0.00'),
        tax: money('0.00'),
      })),
    [cart],
  );

  const totals = useMemo(() => calculateSaleTotals(saleLines), [saleLines]);

  const receiptHeader = { organizationName: user?.organizationName ?? '', storeName: user?.storeName ?? '', customerName };

  function addToCart(product: ShelfProduct): void {
    setCart((current) => {
      const existing = current.find((line) => line.storeProductId === product.id);
      if (existing) {
        return current.map((line) => (line.storeProductId === product.id ? { ...line, quantity: line.quantity + 1 } : line));
      }
      return [...current, { storeProductId: product.id, sku: product.sku, name: product.name, quantity: 1, unitPrice: product.salePrice }];
    });
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
    setCart([]);
    resetTender();
    setCustomerId(null);
    setCustomerName(null);
  }

  const split = splitTender(totals.total.amount, cashReceived, digitalAmount);

  async function checkout(): Promise<void> {
    if (cart.length === 0 || user === null) return;
    if (!split.readable) {
      // Refusing beats guessing. The float parser this replaces turned an
      // unreadable field into 0.00 and carried on, so a mistyped digital amount
      // silently moved the whole sale onto cash or onto the customer's due balance.
      setError('Enter the tendered amounts as plain numbers, e.g. 250 or 250.50');
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
      validateSalePayments(payments, totals.total, { hasCustomer: customerId !== null });
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'The tendered amounts do not add up');
      setBusy(false);
      return;
    }

    const body: SaleCreateRequest = {
      items: cart.map((line) => ({ storeProductId: line.storeProductId, quantity: String(line.quantity) })),
      payments: wirePayments(payments),
      ...(customerId === null ? {} : { customerId }),
      subtotal: totals.subtotal.amount,
      total: totals.total.amount,
    };
    const idempotencyKey = newIdempotencyKey();

    try {
      if (navigator.onLine) {
        const response = await pharmacyApi.sales.create(body, { idempotencyKey });
        setReceipt(receiptFromSale(response.data, receiptHeader));
        clearCart();
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
          setReceipt(provisionalReceipt({ ...receiptHeader, issuedAt: new Date().toISOString(), lines: saleLines, payments }));
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

  async function findCustomer(term: string): Promise<void> {
    if (term.trim() === '') return;
    try {
      const page = await pharmacyApi.customers.search({ q: term.trim() }, { limit: 5 });
      const match = page.items[0];
      if (match) {
        setCustomerId(match.id);
        setCustomerName(`${match.name}${match.normalizedPhone ? ` · ${match.normalizedPhone}` : ''}`);
        setError(null);
      } else {
        setError('No customer matched that name or phone');
      }
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Customer lookup failed');
    }
  }

  const queue: SaleQueueStatus = queueQuery.data ?? emptyQueue;
  const queueProblem = queueQuery.isError && queueQuery.error instanceof Error ? queueQuery.error.message : null;

  return (
    <main className="split-grid split-grid--counter">
      <section style={{ ...card, display: 'flex', flexDirection: 'column', gap: spacing.md }}>
        <h2 style={{ margin: 0, fontSize: tokens.typography.sizes.lg }}>Shelf</h2>
        {/* Said before the first click, not after the sale. A cashier quoting from a
            three-day-old price list should know that is what they are reading. */}
        {stale !== null && <p role="status" style={{ margin: 0, color: colors.warning }}>{stale}</p>}
        {shelfLoad.data?.status === 'unavailable' && (
          <p role="alert" style={{ margin: 0, color: colors.danger }}>{shelfLoad.data.reason}</p>
        )}
        <input
          placeholder="Scan a barcode, or search by name or SKU…"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === 'Enter') submitSearch();
          }}
          style={{ padding: spacing.md, borderRadius: 8, border: `1px solid ${colors.border}` }}
          autoFocus
        />
        <ul style={{ listStyle: 'none', margin: 0, padding: 0, display: 'flex', flexDirection: 'column', gap: spacing.xs, maxHeight: '55vh', overflowY: 'auto' }}>
          {filtered.map((product) => (
            <li key={product.id}>
              <button type="button" onClick={() => addToCart(product)} style={{ ...button, width: '100%', textAlign: 'left', display: 'flex', justifyContent: 'space-between', gap: spacing.sm }}>
                {/* Name first, SKU beneath. A list of bare SKUs is a list picked from
                    memory, which is how the wrong strength gets sold. */}
                <span style={{ display: 'flex', flexDirection: 'column' }}>
                  {product.name}
                  <span style={{ color: colors.muted, fontSize: tokens.typography.sizes.sm }}>
                    {product.sku}
                    {product.rack === null ? '' : ` · ${product.rack}`}
                  </span>
                </span>
                <span style={{ color: colors.muted }}>৳{product.salePrice}</span>
              </button>
            </li>
          ))}
          {filtered.length === 0 && (
            <li style={{ color: colors.muted }}>
              {products.length === 0 ? 'No shelf cached in this browser yet — connect once to load it.' : 'No products match.'}
            </li>
          )}
        </ul>
      </section>

      <section style={{ ...card, display: 'flex', flexDirection: 'column', gap: spacing.md }}>
        <h2 style={{ margin: 0, fontSize: tokens.typography.sizes.lg }}>Cart</h2>
        <ul style={{ listStyle: 'none', margin: 0, padding: 0, display: 'flex', flexDirection: 'column', gap: spacing.xs }}>
          {cart.map((line) => (
            <li key={line.storeProductId} style={{ display: 'flex', alignItems: 'center', gap: spacing.sm }}>
              <span style={{ flex: 1 }}>{line.name} · ৳{line.unitPrice}</span>
              <input
                type="number"
                min={0}
                value={line.quantity}
                onChange={(event) => setQuantity(line.storeProductId, event.target.value)}
                style={{ width: 64, padding: spacing.xs, borderRadius: 6, border: `1px solid ${colors.border}` }}
              />
              <strong>৳{lineTotal(line).amount}</strong>
            </li>
          ))}
          {cart.length === 0 && <li style={{ color: colors.muted }}>Empty.</li>}
        </ul>

        <div style={{ display: 'flex', justifyContent: 'space-between', borderTop: `1px solid ${colors.border}`, paddingTop: spacing.md }}>
          <span>Subtotal</span>
          <span>৳{totals.subtotal.amount}</span>
        </div>
        <div style={{ display: 'flex', justifyContent: 'space-between' }}>
          <span>Total</span>
          <strong style={{ fontSize: tokens.typography.sizes.lg }}>৳{totals.total.amount}</strong>
        </div>

        <div style={{ display: 'flex', gap: spacing.sm }}>
          <input
            placeholder="Customer name or phone"
            onKeyDown={(event) => {
              if (event.key === 'Enter') void findCustomer((event.target as HTMLInputElement).value);
            }}
            style={{ flex: 1, padding: spacing.sm, borderRadius: 8, border: `1px solid ${colors.border}` }}
          />
          {customerId !== null && (
            <button type="button" style={button} onClick={() => { setCustomerId(null); setCustomerName(null); }}>
              {customerName ?? 'Customer'} ✕
            </button>
          )}
        </div>

        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: spacing.sm }}>
          <label style={{ display: 'flex', flexDirection: 'column', gap: spacing.xs, fontSize: tokens.typography.sizes.sm }}>
            Cash received
            <input value={cashReceived} onChange={(event) => setCashReceived(event.target.value)} placeholder={totals.total.amount} inputMode="decimal" style={{ padding: spacing.sm, borderRadius: 8, border: `1px solid ${colors.border}` }} />
          </label>
          <label style={{ display: 'flex', flexDirection: 'column', gap: spacing.xs, fontSize: tokens.typography.sizes.sm }}>
            Digital ({digitalMethod}) amount
            <input value={digitalAmount} onChange={(event) => setDigitalAmount(event.target.value)} placeholder="0.00" inputMode="decimal" style={{ padding: spacing.sm, borderRadius: 8, border: `1px solid ${colors.border}` }} />
          </label>
        </div>
        <div style={{ display: 'flex', gap: spacing.sm }}>
          {(['bkash', 'nagad'] as const).map((method) => (
            <button key={method} type="button" onClick={() => setDigitalMethod(method)} style={{ ...button, flex: 1, background: digitalMethod === method ? colors.primary : colors.surface, color: digitalMethod === method ? colors.primaryForeground : colors.foreground }}>
              {method}
            </button>
          ))}
        </div>

        <div style={{ display: 'flex', justifyContent: 'space-between', color: colors.muted, fontSize: tokens.typography.sizes.sm }}>
          <span>Cash ৳{split.cash} · {digitalMethod} ৳{split.digital}</span>
          <span style={{ color: split.due === '0.00' ? colors.muted : colors.warning }}>Due ৳{split.due}</span>
        </div>
        {!split.readable && (
          <p role="alert" style={{ margin: 0, color: colors.danger, fontSize: tokens.typography.sizes.sm }}>
            One of the tendered amounts is not a number. Charging is blocked until it is corrected.
          </p>
        )}
        {/* Change is displayed, not inferred: the cashier has to know what to hand
            back, and the server records `receivedAmount` so the drawer reconciles. */}
        {split.change !== '0.00' && (
          <div style={{ display: 'flex', justifyContent: 'space-between', fontWeight: tokens.typography.weights.semibold }}>
            <span>Change due</span>
            <strong style={{ fontSize: tokens.typography.sizes.lg }}>৳{split.change}</strong>
          </div>
        )}

        <button type="button" disabled={busy || cart.length === 0} onClick={() => void checkout()} style={{ ...button, background: colors.primary, color: colors.primaryForeground, border: 'none', padding: spacing.md }}>
          {busy ? 'Charging…' : 'Complete sale'}
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
        {receipt !== null && <ReceiptPanel receipt={receipt} />}
      </section>
    </main>
  );
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
 * The slip, from one model whether the sale was filed or queued.
 *
 * `window.print` prints the page, so the text form is offered alongside it: it is
 * what a thermal printer takes, and it is the same string the desktop till sends
 * to `hardware.printReceipt`.
 */
function ReceiptPanel({ receipt }: { receipt: ReceiptModel }): ReactNode {
  return (
    <div style={{ borderTop: `1px solid ${colors.border}`, paddingTop: spacing.md }}>
      <h3 style={{ margin: `0 0 ${spacing.xs}` }}>
        {receipt.receiptNumber === null ? 'Receipt pending upload' : `Receipt ${receipt.receiptNumber}`}
      </h3>
      {receipt.receiptNumber === null && (
        <p style={{ margin: `0 0 ${spacing.xs}`, color: colors.warning, fontSize: tokens.typography.sizes.sm }}>
          {/* Said plainly: the number is assigned by the server, and inventing one
              here would hand out a number that later belongs to a different sale. */}
          This sale is queued on this device. Its number is issued when it uploads.
        </p>
      )}
      <ul style={{ listStyle: 'none', margin: 0, paddingLeft: 0, fontSize: tokens.typography.sizes.sm, color: colors.muted }}>
        {receipt.lines.map((line, index) => (
          <li key={`${line.name}-${index}`}>{line.name} × {line.quantity} = ৳{line.lineTotal.amount}</li>
        ))}
      </ul>
      <p style={{ margin: `${spacing.xs} 0` }}>
        <strong>Total ৳{receipt.totals.total.amount}</strong> · {receipt.payments.map((payment) => `${payment.method} ৳${payment.amount.amount}`).join(', ')}
        {receipt.change.amount === '0.00' ? '' : ` · change ৳${receipt.change.amount}`}
      </p>
      <p style={{ margin: 0, fontSize: tokens.typography.sizes.sm, color: colors.muted }}>{receipt.organizationName}</p>
      <div style={{ display: 'flex', gap: spacing.sm, marginTop: spacing.sm }}>
        <button type="button" style={button} onClick={() => window.print()}>Print</button>
        <button type="button" style={button} onClick={() => void navigator.clipboard?.writeText(formatReceiptText(receipt))}>
          Copy slip text
        </button>
      </div>
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

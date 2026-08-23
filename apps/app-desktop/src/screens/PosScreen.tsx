import type { SaleCreateRequest } from '@pharmacy/api';
import {
  calculateSaleTotals,
  formatReceiptText,
  provisionalReceipt,
  receiptFromSale,
  splitTender,
  validateSalePayments,
  wirePayments,
  type Receipt,
} from '@pharmacy/sales';
import { money, multiply, type MoneyValue } from '@pharmacy/money';
import { colors, spacing, tokens } from '@pharmacy/design-tokens';
import { envelopeContextFor, loadShelf, matchShelf, submitShelfEntry, type ShelfProduct, type StuckEntry } from '@pharmacy/sync';
import { useCallback, useEffect, useMemo, useRef, useState, type CSSProperties, type KeyboardEvent, type ReactNode } from 'react';

import { pharmacyApi } from '../lib/api';
import { AUTO_FLUSH_INTERVAL_MS, enqueueFlush, forgetSale, queueSale, queueStatus, recoverOutbox, type Ingest, type SaleQueueStatus } from '../lib/offlineQueue';
import { useSession } from '../lib/session';
import { shelf } from '../lib/shelf';
import { buildPayments, defaultDigitalMethod, digitalLabel, digitalMethods, type DigitalMethodChoice } from '../lib/tender';
import { desktopPlatform } from '../platform/runtime';

type CartLine = { storeProductId: string; sku: string; name: string; quantity: number; unitPrice: string };

const card: CSSProperties = { background: colors.surface, border: `1px solid ${colors.border}`, borderRadius: 12, padding: spacing.lg };
const input: CSSProperties = { padding: spacing.sm, borderRadius: 8, border: `1px solid ${colors.border}`, boxSizing: 'border-box' };
const button: CSSProperties = { ...input, cursor: 'pointer', background: colors.primary, color: colors.primaryForeground, border: 'none', fontWeight: tokens.typography.weights.medium };

const emptyQueue: SaleQueueStatus = { pending: 0, retrying: 0, stuck: [], nextRetryAt: null };

/**
 * Keyboard-first counter: type or scan, Enter rings up an unambiguous match; F2
 * charges the sale across cash and an optional bKash/Nagad tender; F3 prints the
 * last receipt; F9 uploads the offline queue.
 *
 * A USB barcode gun needs nothing further from this screen -- it types the digits
 * into the search box and sends Enter, which is the path already here.
 */
export function PosScreen(): ReactNode {
  const { user, signOut } = useSession();
  const [cache, setCache] = useState<readonly ShelfProduct[]>([]);
  const [stale, setStale] = useState<string | null>(null);
  const [query, setQuery] = useState('');
  const [cart, setCart] = useState<CartLine[]>([]);
  const [cashReceived, setCashReceived] = useState('');
  const [digitalAmount, setDigitalAmount] = useState('');
  const [digitalMethod, setDigitalMethod] = useState<DigitalMethodChoice>(defaultDigitalMethod);
  const [receipt, setReceipt] = useState<Receipt | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [queue, setQueue] = useState<SaleQueueStatus>(emptyQueue);
  const searchRef = useRef<HTMLInputElement>(null);

  const refreshQueue = useCallback(() => {
    void queueStatus()
      .then(setQueue)
      // An unreadable queue must not take the till down with it, and it must not
      // read as "everything uploaded" either.
      .catch((cause: unknown) => setError(cause instanceof Error ? cause.message : 'Could not read the offline sale queue'));
  }, []);

  const storeId = user?.storeId ?? null;

  useEffect(() => {
    // A sale left mid-upload by a killed app is invisible to the flush until it is
    // put back in line, so this runs before anything reads the queue.
    void recoverOutbox().then(refreshQueue, refreshQueue);
    searchRef.current?.focus();
  }, [refreshQueue]);

  useEffect(() => {
    if (storeId === null) return;
    let cancelled = false;
    void (async () => {
      // The comment here used to promise that "the counter keeps selling through
      // connectivity drops" while the shelf lived in `useState` and was refetched on
      // every mount -- so a till restarted during an outage had no shelf at all, and
      // the offline outbox beneath it could never receive a sale. It is on disk now,
      // and `loadShelf` decides that a failed fetch behind a cache is not an error.
      const load = await loadShelf(
        shelf,
        storeId,
        async () => (await pharmacyApi.products.listCurrentStoreProducts()).items,
        (cached) => {
          if (!cancelled) setCache(cached);
        },
      );
      if (cancelled) return;
      if (load.status === 'unavailable') {
        setError(load.reason);
        return;
      }
      setCache(load.products);
      setStale(load.status === 'stale' ? load.note : null);
    })();
    return () => {
      cancelled = true;
    };
  }, [storeId]);

  const matches = useMemo(() => {
    // Barcode first, then exact SKU, then a substring of either the SKU or the name.
    // The old filter read SKUs only, so a scanned barcode found nothing and a cashier
    // who knew the medicine by name had to know its code as well.
    if (query.trim() === '') return cache.slice(0, 8);
    return matchShelf(cache, query)
      .map((match) => match.product)
      .slice(0, 8);
  }, [cache, query]);

  const saleLines = useMemo(
    () =>
      cart.map((line) => ({
        id: line.storeProductId,
        productId: line.storeProductId,
        // The product name, so a printed slip reads "Paracetamol 500mg × 2" rather
        // than "PARA-500 × 2" -- the SKU was all the shelf endpoint used to return.
        name: line.name,
        quantity: line.quantity,
        unitPrice: money(line.unitPrice),
        discount: money('0.00'),
        tax: money('0.00'),
      })),
    [cart],
  );

  const totals = useMemo(() => calculateSaleTotals(saleLines), [saleLines]);

  const receiptHeader = { organizationName: user?.organizationName ?? '', storeName: user?.storeName ?? '' };

  // The till has no customer picker, so there is no due account to fall back on:
  // a shortfall after both tenders is refused rather than quietly booked against
  // nobody.
  const split = splitTender(totals.total.amount, cashReceived, digitalAmount);

  function addToCart(product: ShelfProduct): void {
    setReceipt(null);
    setMessage(null);
    setCart((current) => {
      const existing = current.find((line) => line.storeProductId === product.id);
      if (existing) return current.map((line) => (line.storeProductId === product.id ? { ...line, quantity: line.quantity + 1 } : line));
      return [...current, { storeProductId: product.id, sku: product.sku, name: product.name, quantity: 1, unitPrice: product.salePrice }];
    });
    setQuery('');
    searchRef.current?.focus();
  }

  const charge = useCallback(async () => {
    if (cart.length === 0 || user === null) return;
    setError(null);
    setMessage(null);
    if (!split.readable) {
      setError('Enter the tendered amounts as plain numbers, e.g. 250 or 250.50');
      return;
    }
    if (split.due !== '0.00') {
      // Blank cash means the exact total, so a due line here is a shortfall after
      // both tenders. With no customer on the sale the server refuses it (`Due
      // payments require a customer on the sale`), and there is nowhere else for
      // the difference to go.
      setError(`Tendered amounts are short by ৳${split.due}. Take the full amount, or ring this sale up on the web counter to put it on an account.`);
      return;
    }
    // The chosen wallet names only the digital row; the cash row stays `cash`,
    // which is what the drawer reconciles against.
    const payments = buildPayments(split, digitalMethod);
    try {
      // Checked before posting: every rule here is one the server enforces after
      // the fact, by which point the cart is cleared and the customer has gone.
      validateSalePayments(payments, totals.total, { hasCustomer: false });
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'The tendered amounts do not add up');
      return;
    }
    const body: SaleCreateRequest = {
      items: cart.map((line) => ({ storeProductId: line.storeProductId, quantity: String(line.quantity) })),
      payments: wirePayments(payments),
      subtotal: totals.subtotal.amount,
      total: totals.total.amount,
    };
    try {
      const response = await pharmacyApi.sales.create(body, { idempotencyKey: `dpos-${crypto.randomUUID()}` });
      setReceipt(receiptFromSale(response.data, receiptHeader));
      setCart([]);
      setCashReceived('');
      setDigitalAmount('');
    } catch (cause) {
      const offline = (cause as { code?: string }).code === 'NETWORK_ERROR';
      if (offline) {
        const context = envelopeContextFor(user);
        if (context === null) {
          // No device row means `/sync/events` would answer DEVICE_CONTEXT_REQUIRED,
          // so queueing would only hide the sale somewhere it can never leave.
          setError('Offline, and this till is not registered for offline sales yet. Sign in again once the server is reachable; keep the cart open and write this sale down.');
          return;
        }
        try {
          await queueSale(body, context);
          // The customer still gets a slip, with no receipt number on it: that
          // number is the server's to assign, and one invented here would later
          // belong to a different sale.
          setReceipt(provisionalReceipt({ ...receiptHeader, issuedAt: new Date().toISOString(), lines: saleLines, payments }));
          setMessage('Offline — sale queued. It uploads by itself, or F9 right now. F3 prints the slip.');
          setCart([]);
          setCashReceived('');
          setDigitalAmount('');
        } catch (writeFailure) {
          // `queueSale` rejects when the store cannot reach disk, and that has to be
          // said out loud. Saying "queued" and clearing the cart would leave the
          // cashier believing the sale was safe when nothing was written -- the exact
          // loss the durable store was added to prevent, only quieter. The cart stays
          // up because it is the only record of the sale that exists.
          setError(`Offline and this sale could not be saved on this machine (${writeFailure instanceof Error ? writeFailure.message : String(writeFailure)}). Keep the cart open and write it down.`);
        }
      } else {
        // The cart survives a rejection deliberately: the sale reached the server
        // and was refused, so nothing is recorded anywhere and this cart is the
        // only evidence of what was scanned.
        setError(cause instanceof Error ? cause.message : 'Charge failed');
      }
    } finally {
      refreshQueue();
    }
  }, [cart, saleLines, split, totals, digitalMethod, refreshQueue, receiptHeader, user]);

  const ingest: Ingest = useCallback(async (events) => (await pharmacyApi.sync.ingest(events)).data.acks, []);

  const uploadQueue = useCallback(async () => {
    setError(null);
    setMessage(null);
    try {
      // Straight to `/sync/events`, which answers per event: a sale the server
      // cannot take yet is held and retried while the rest go through. Each entry
      // used to be replayed against `/sales` instead, which left device identity,
      // client sequences and per-event acks unexercised by the client needing them.
      // Manual flushes always run; `enqueueFlush` only serializes them behind any
      // automatic flush already in flight.
      const result = await enqueueFlush(ingest);
      if (result === null) {
        setMessage('Nothing to upload yet.');
        return;
      }
      const accepted = result.uploaded + result.duplicates;
      const parts = [`${accepted} uploaded`];
      if (result.retrying > 0) parts.push(`${result.retrying} will retry`);
      if (result.rejected > 0) parts.push(`${result.rejected} need re-entering`);
      setMessage(parts.join(' · '));
      setError(result.firstError);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Upload failed');
    } finally {
      refreshQueue();
    }
  }, [ingest, refreshQueue]);

  useEffect(() => {
    // Offline sales now upload themselves. A tick asks the engine whether anything
    // is actually sendable first (`onlyIfDue`), so a queue sitting out its backoff
    // is left alone instead of being poked into another failure every interval;
    // focus counts too, since the till regaining attention is the cheapest signal
    // that connectivity may be back. Failures stay quiet here beyond the badge:
    // the stuck list carries refusals, and an unattended till should not collect
    // error banners all shift.
    const attempt = (): void => {
      void enqueueFlush(ingest, { onlyIfDue: true })
        .then((result) => {
          if (result !== null) refreshQueue();
        })
        .catch((cause: unknown) => {
          setError(cause instanceof Error ? cause.message : 'Automatic upload failed');
          refreshQueue();
        });
    };
    const timer = window.setInterval(attempt, AUTO_FLUSH_INTERVAL_MS);
    window.addEventListener('focus', attempt);
    return () => {
      window.clearInterval(timer);
      window.removeEventListener('focus', attempt);
    };
  }, [ingest, refreshQueue]);

  const printLast = useCallback(async () => {
    if (receipt === null) return;
    const platform = await desktopPlatform();
    // One slip format, shared with the web counter: an offline sale prints the same
    // way a filed one does, saying it has no number yet rather than inventing one.
    const result = await platform.hardware.printReceipt(formatReceiptText(receipt));
    if (!result.ok) setError(`Printer: ${result.reason}`);
  }, [receipt]);

  function setQuantity(storeProductId: string, raw: string): void {
    // Parsed and guarded here because the value flows into `calculateSaleTotals`
    // during render, and `multiply` throws on a quantity that is not a whole
    // number. Clearing the field yields `NaN`, so the previous inline
    // `Number(...)` handler blanked the till screen and lost the cart -- on the
    // keyboard-first shell, where backspace in a quantity box is routine.
    const trimmed = raw.trim();
    if (trimmed === '') return; // mid-edit; the last good quantity stays on screen
    const quantity = Number(trimmed);
    if (!Number.isInteger(quantity)) return;
    setCart((current) =>
      quantity <= 0
        ? current.filter((entry) => entry.storeProductId !== storeProductId)
        : current.map((entry) => (entry.storeProductId === storeProductId ? { ...entry, quantity } : entry)),
    );
  }

  function onKeyDown(event: KeyboardEvent<HTMLDivElement>): void {
    if (event.key === 'F2') {
      event.preventDefault();
      void charge();
    } else if (event.key === 'F3') {
      event.preventDefault();
      void printLast();
    } else if (event.key === 'F9') {
      event.preventDefault();
      void uploadQueue();
    } else if (event.key === 'Enter' && document.activeElement === searchRef.current) {
      event.preventDefault();
      // Not `matches[0]` any more. That was survivable while the search read SKUs
      // only; once it reads names too, "para" and Enter would have sold whichever of
      // Paracetamol 500mg and 650mg sorted first. `submitShelfEntry` rings up a
      // barcode or a SKU typed in full and leaves anything partial to the list.
      const entry = submitShelfEntry(cache, query);
      if (entry.status === 'product') addToCart(entry.product);
    }
  }

  return (
    <div tabIndex={-1} onKeyDown={onKeyDown} style={{ minHeight: '100vh', background: colors.background, color: colors.foreground, fontFamily: tokens.typography.family, outline: 'none' }}>
      <header style={{ display: 'flex', alignItems: 'center', gap: spacing.xl, padding: `${spacing.md} ${spacing.xl}`, background: colors.surface, borderBottom: `1px solid ${colors.border}` }}>
        <strong>{user?.organizationName ?? ''}</strong>
        <span style={{ color: colors.muted }}>{user?.storeName ?? ''} · {user?.role ?? ''}</span>
        <span style={{ flex: 1 }} />
        <span style={{ color: queue.pending > 0 ? colors.warning : colors.muted }}>{queue.pending > 0 ? `${queue.pending} queued (F9)` : 'synced'}</span>
        <button type="button" style={{ ...input, cursor: 'pointer' }} onClick={() => void signOut()}>Sign out</button>
      </header>

      <main style={{ display: 'grid', gridTemplateColumns: '1.4fr 1fr', gap: spacing.lg, padding: spacing.lg, alignItems: 'start' }}>
        <section style={{ ...card, display: 'flex', flexDirection: 'column', gap: spacing.md }}>
          <h2 style={{ margin: 0, fontSize: tokens.typography.sizes.lg }}>Shelf cache ({cache.length})</h2>
          {/* Said before the first keystroke, not after the sale. On a keyboard-first
              till the cashier never looks anywhere else on the screen. */}
          {stale !== null && <p role="status" style={{ margin: 0, color: colors.warning, fontSize: tokens.typography.sizes.sm }}>{stale}</p>}
          <input ref={searchRef} style={input} value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Scan, or type a name or SKU — Enter rings up an exact match" />
          <ul style={{ listStyle: 'none', margin: 0, padding: 0, display: 'flex', flexDirection: 'column', gap: spacing.xs }}>
            {matches.map((product) => (
              <li key={product.id}>
                <button type="button" style={{ ...input, width: '100%', textAlign: 'left', cursor: 'pointer', display: 'flex', justifyContent: 'space-between', gap: spacing.sm }} onClick={() => addToCart(product)}>
                  {/* Name first, SKU beneath. A list of bare SKUs is a list picked
                      from memory, which is how the wrong strength gets sold. */}
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
            {cache.length === 0 && (
              <li style={{ color: colors.muted }}>No shelf on this till yet — connect once to load it.</li>
            )}
          </ul>
          <p style={{ margin: 0, color: colors.muted, fontSize: tokens.typography.sizes.sm }}>F2 charge · F3 print · F9 upload queue</p>
        </section>

        <section style={{ ...card, display: 'flex', flexDirection: 'column', gap: spacing.md }}>
          <h2 style={{ margin: 0, fontSize: tokens.typography.sizes.lg }}>Cart</h2>
          {cart.map((line) => (
            <div key={line.storeProductId} style={{ display: 'flex', alignItems: 'center', gap: spacing.sm }}>
              <span style={{ flex: 1 }}>{line.name} × {line.quantity}</span>
              <input
                type="number"
                min={0}
                value={line.quantity}
                onChange={(event) => setQuantity(line.storeProductId, event.target.value)}
                style={{ ...input, width: 64 }}
              />
              <span>৳{lineTotal(line).amount}</span>
            </div>
          ))}
          {cart.length === 0 && <p style={{ color: colors.muted, margin: 0 }}>Empty.</p>}
          <div style={{ display: 'flex', justifyContent: 'space-between', borderTop: `1px solid ${colors.border}`, paddingTop: spacing.md }}>
            <span style={{ fontWeight: tokens.typography.weights.semibold }}>Total</span>
            <span style={{ fontWeight: tokens.typography.weights.bold, fontSize: tokens.typography.sizes.lg }}>৳{totals.total.amount}</span>
          </div>
          <label style={{ display: 'flex', flexDirection: 'column', gap: spacing.xs, fontSize: tokens.typography.sizes.sm }}>
            Cash received (blank = exact)
            <input value={cashReceived} onChange={(event) => setCashReceived(event.target.value)} placeholder={totals.total.amount} inputMode="decimal" style={input} />
          </label>
          <div style={{ display: 'flex', gap: spacing.sm, alignItems: 'stretch' }}>
            <label style={{ display: 'flex', flexDirection: 'column', gap: spacing.xs, fontSize: tokens.typography.sizes.sm, flex: 1 }}>
              {digitalLabel(digitalMethod)} amount (blank = none)
              <input value={digitalAmount} onChange={(event) => setDigitalAmount(event.target.value)} placeholder="0.00" inputMode="decimal" style={input} />
            </label>
            {digitalMethods.map((method) => (
              <button
                key={method}
                type="button"
                onClick={() => setDigitalMethod(method)}
                style={{
                  ...input,
                  cursor: 'pointer',
                  alignSelf: 'flex-end',
                  background: digitalMethod === method ? colors.primary : colors.surface,
                  color: digitalMethod === method ? colors.primaryForeground : colors.foreground,
                  fontWeight: tokens.typography.weights.medium,
                }}
              >
                {digitalLabel(method)}
              </button>
            ))}
          </div>
          {/* The split, said out loud before charging: which tender took what, and
              what would be left on an account if it does not cover the total. */}
          <div style={{ display: 'flex', justifyContent: 'space-between', color: colors.muted, fontSize: tokens.typography.sizes.sm }}>
            <span>Cash ৳{split.cash} · {digitalLabel(digitalMethod)} ৳{split.digital}</span>
            <span style={{ color: split.due === '0.00' ? colors.muted : colors.warning }}>Due ৳{split.due}</span>
          </div>
          {!split.readable && (
            <p role="alert" style={{ margin: 0, color: colors.danger, fontSize: tokens.typography.sizes.sm }}>
              A tendered amount is not a number. Charging is blocked until it is corrected.
            </p>
          )}
          {/* Change is shown, not inferred: the cashier has to know what to hand back,
              and the server records `receivedAmount` so the drawer reconciles. */}
          {split.change !== '0.00' && (
            <div style={{ display: 'flex', justifyContent: 'space-between', fontWeight: tokens.typography.weights.semibold }}>
              <span>Change due</span>
              <strong style={{ fontSize: tokens.typography.sizes.lg }}>৳{split.change}</strong>
            </div>
          )}
          <button type="button" style={button} disabled={cart.length === 0} onClick={() => void charge()}>Charge sale (F2)</button>
          {queue.pending > 0 && (
            <button type="button" style={{ ...input, cursor: 'pointer' }} onClick={() => void uploadQueue()}>
              Upload {queue.pending} queued sale(s) (F9)
              {queue.nextRetryAt !== null ? ` — next retry ${new Date(queue.nextRetryAt).toLocaleTimeString()}` : ''}
            </button>
          )}
          {queue.stuck.length > 0 && (
            <div style={{ border: `1px solid ${colors.danger}`, borderRadius: 8, padding: spacing.sm, display: 'flex', flexDirection: 'column', gap: spacing.xs }}>
              {/* Refused sales are listed, never dropped: each is money already taken
                  for stock already handed over, and the queued payload is the only
                  record left of it. Clearing one is a person's decision after
                  re-entering the sale, not a consequence of a failed upload. */}
              <strong style={{ color: colors.danger }}>{queue.stuck.length} sale(s) the server will not accept</strong>
              <span style={{ fontSize: tokens.typography.sizes.sm }}>Re-enter each one, then clear it.</span>
              {queue.stuck.map((entry: StuckEntry<SaleCreateRequest>) => (
                <div key={entry.eventId} style={{ display: 'flex', alignItems: 'baseline', gap: spacing.sm, fontSize: tokens.typography.sizes.sm }}>
                  <span style={{ flex: 1 }}>
                    ৳{entry.payload.total ?? '?'} · {entry.payload.items.length} line(s) · {new Date(entry.createdAt).toLocaleString()}
                    <br />
                    <span style={{ color: colors.muted }}>{entry.reason ?? 'Rejected'}</span>
                  </span>
                  <button type="button" style={{ ...input, cursor: 'pointer' }} onClick={() => void forgetSale(entry.eventId).then(refreshQueue)}>
                    Re-entered — clear
                  </button>
                </div>
              ))}
            </div>
          )}
          {message !== null && <p style={{ margin: 0, color: colors.warning }}>{message}</p>}
          {error !== null && <p role="alert" style={{ margin: 0, color: colors.danger }}>{error}</p>}
          {receipt !== null && (
            <div style={{ borderTop: `1px solid ${colors.border}`, paddingTop: spacing.md }}>
              <h3 style={{ margin: `0 0 ${spacing.xs}` }}>
                {receipt.receiptNumber === null ? 'Receipt pending upload' : `Receipt ${receipt.receiptNumber}`}
              </h3>
              {receipt.receiptNumber === null && (
                <p style={{ margin: `0 0 ${spacing.xs}`, color: colors.warning, fontSize: tokens.typography.sizes.sm }}>
                  Queued on this till. Its number is issued when it uploads.
                </p>
              )}
              {receipt.lines.map((line, index) => (
                <p key={`${line.name}-${index}`} style={{ margin: 0, color: colors.muted }}>{line.name} × {line.quantity} = ৳{line.lineTotal.amount}</p>
              ))}
              <p style={{ margin: `${spacing.xs} 0` }}>
                <strong>Total ৳{receipt.totals.total.amount}</strong>
                {receipt.change.amount === '0.00' ? '' : ` · change ৳${receipt.change.amount}`}
              </p>
              <button type="button" style={{ ...input, cursor: 'pointer' }} onClick={() => void printLast()}>Print (F3)</button>
            </div>
          )}
        </section>
      </main>
    </div>
  );
}

function lineTotal(line: CartLine): MoneyValue {
  return multiply(money(line.unitPrice), line.quantity);
}

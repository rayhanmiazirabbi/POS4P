import type { CatalogAlternativeItem, Customer, SaleCreateRequest } from '@pharmacy/api';
import {
  calculateSaleTotals,
  formatReceiptText,
  provisionalReceipt,
  receiptFromSale,
  splitTender,
  tenderPayments,
  validateSalePayments,
  wirePayments,
  type Receipt,
} from '@pharmacy/sales';
import {
  describeMedicineMatch,
  findMedicineAlternatives,
  highlightMedicineSpans,
  loadShelf,
  medicineMatchesAreFuzzy,
  mergeMedicineAlternatives,
  scanShelf,
  submitShelfEntry,
  type ShelfProduct,
} from '@pharmacy/sync';
import { money, multiply } from '@pharmacy/money';
import { useMutation, useQuery } from '@tanstack/react-query';
import { Link } from 'expo-router';
import { useCallback, useEffect, useMemo, useReducer, useRef, useState, type ReactNode } from 'react';
import { ActivityIndicator, Button, FlatList, Modal, Pressable, ScrollView, Share, Text, TextInput, View } from 'react-native';

import { pharmacyApi } from '../../src/lib/api';
import { buildMedicineListEntries } from '../../src/lib/medicineList';
import { RequireCapability } from '../../src/lib/guard';
import { envelopeContext, newIdempotencyKey, queueSale, queueStatus, recoverOutbox } from '../../src/lib/offlineSales';
import { CameraView, nativeScanner, scannerFormats, type ScannerPermission } from '../../src/platform';
import { useSession } from '../../src/lib/session';
import { shelf } from '../../src/lib/shelf';
import { useBackgroundSync } from '../../src/lib/useBackgroundSync';

type CartLine = { storeProductId: string; sku: string; name: string; quantity: number; unitPrice: string };

export default function PosScreen(): ReactNode {
  return (
    <RequireCapability capability="sales.create">
      <PosCounter />
    </RequireCapability>
  );
}

function PosCounter(): ReactNode {
  const { user } = useSession();
  const [stale, setStale] = useState<string | null>(null);
  const [query, setQuery] = useState('');
  const [scanning, setScanning] = useState(false);
  /** The shelf row whose alternatives sheet is open, or none. */
  const [altFor, setAltFor] = useState<ShelfProduct | null>(null);
  const [cart, setCart] = useState<CartLine[]>([]);
  const [customerId, setCustomerId] = useState<string | null>(null);
  const [customerName, setCustomerName] = useState<string | null>(null);
  const [customerTerm, setCustomerTerm] = useState('');
  /** Ambiguous lookup results awaiting an explicit pick; empty once settled. */
  const [customerOptions, setCustomerOptions] = useState<readonly Customer[]>([]);
  const [received, setReceived] = useState('');
  const [digitalAmount, setDigitalAmount] = useState('');
  const [digitalMethod, setDigitalMethod] = useState<'bkash' | 'nagad'>('bkash');
  const [receipt, setReceipt] = useState<Receipt | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  /** Bumped to re-read the queue after recover, checkout and every flush attempt. */
  const [queueTick, bumpQueueTick] = useReducer((count: number) => count + 1, 0);
  /** The cached shelf painted before the network answers, keyed by its branch. */
  const [paintedCache, setPaintedCache] = useState<{ storeId: string; products: readonly ShelfProduct[] } | null>(null);
  /** The last accepted scan, so one carton in front of the lens is one unit. */
  const lastScan = useRef<{ code: string; at: number } | null>(null);

  const storeId = user?.storeId ?? null;

  // The queue count is server-independent local state, but a query keyed on the
  // tick keeps every bump -- recover, checkout, manual or automatic flush --
  // reading it fresh through one code path.
  const queueQuery = useQuery({ queryKey: ['outbox-status', queueTick], queryFn: () => queueStatus(), retry: false });
  const pending = queueQuery.data?.pending ?? 0;

  useEffect(() => {
    if (queueQuery.error === null) return;
    // An unreadable queue must not take the till down with it, and it must not
    // read as "all uploaded" either.
    setError(queueQuery.error instanceof Error ? queueQuery.error.message : 'Could not read the offline queue');
  }, [queueQuery.error]);

  useEffect(() => {
    // A sale left mid-upload by a killed app is invisible to `dueForUpload` until
    // it is put back in line, so this runs before anything reads the queue.
    void recoverOutbox().then(bumpQueueTick, bumpQueueTick);
  }, []);

  // Automatic uploads: interval plus foreground transitions, both gated on the
  // engine's own due-decision. `flushNow` shares the gate with them.
  const { flushNow } = useBackgroundSync(useCallback(() => bumpQueueTick(), []));

  const shelfQuery = useQuery({
    queryKey: ['shelf', storeId],
    enabled: storeId !== null,
    // One attempt per key: `loadShelf` already owns the cache fallback policy,
    // so a failed fetch resolves rather than rejects and retrying would only
    // delay the stale shelf getting on screen.
    retry: false,
    queryFn: async () => {
      const sid = storeId;
      if (sid === null) throw new Error('No store is signed in');
      return loadShelf(
        await shelf(),
        sid,
        async () => (await pharmacyApi.products.listCurrentStoreProducts()).items,
        (cached) => setPaintedCache({ storeId: sid, products: cached }),
      );
    },
  });

  useEffect(() => {
    const loaded = shelfQuery.data;
    if (loaded === undefined) return;
    if (loaded.status === 'unavailable') {
      setError(loaded.reason);
      return;
    }
    setStale(loaded.status === 'stale' ? loaded.note : null);
  }, [shelfQuery.data]);

  const products = useMemo<readonly ShelfProduct[]>(() => {
    const loaded = shelfQuery.data;
    if (loaded !== undefined && loaded.status !== 'unavailable') return loaded.products;
    // While the network is still deciding, or once it has failed behind a
    // usable cache, what was painted from disk is what the counter sells from.
    return paintedCache !== null && paintedCache.storeId === storeId ? paintedCache.products : [];
  }, [shelfQuery.data, paintedCache, storeId]);

  const searching = query.trim() !== '';
  // The search box is also the scanner input, so one matcher answers both: a
  // barcode ranks above an exact SKU, which ranks above a substring of either
  // the SKU or the name -- and now a conservative typo guess below both.
  // Grouping only while a query is active: the untouched box keeps the plain
  // shelf list this screen has always shown between customers.
  const { entries: listData, matches } = useMemo(
    () => buildMedicineListEntries(products, query),
    [products, query],
  );

  const saleLines = useMemo(
    () =>
      cart.map((line) => ({
        id: line.storeProductId,
        productId: line.storeProductId,
        // The product name, so a receipt reads "Paracetamol 500mg × 2" instead of
        // "PARA-500 × 2". The SKU was all the shelf carried before it was joined.
        name: line.name,
        quantity: line.quantity,
        unitPrice: money(line.unitPrice),
        discount: money('0.00'),
        tax: money('0.00'),
      })),
    [cart],
  );

  const totals = useMemo(() => calculateSaleTotals(saleLines), [saleLines]);

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
   * A barcode, or a SKU typed in full, is rung up; a partial name narrows the list
   * instead. `submitShelfEntry` holds that rule for all three counters -- the phone
   * must not be the one that guesses between two strengths.
   */
  function submit(code: string): void {
    const entry = submitShelfEntry(products, code);
    if (entry.status !== 'product') return;
    addToCart(entry.product);
    setQuery('');
    setError(null);
  }

  function onScanned(code: string): void {
    const now = Date.now();
    const previous = lastScan.current;
    // A camera reports the same carton on every frame. Without this, one box held in
    // front of the lens for a second is twenty units on the receipt.
    if (previous !== null && previous.code === code && now - previous.at < 1500) return;
    lastScan.current = { code, at: now };

    const scan = scanShelf(products, code);
    if (scan.status === 'product') {
      addToCart(scan.product);
      // The camera stays open. A basket is several cartons, and closing between each
      // one is slower than the keypad this is meant to replace.
      setMessage(`Added ${scan.product.name}`);
      setError(null);
      return;
    }
    // Not certain, so the camera closes and a person decides. The code goes into the
    // search box rather than being discarded: it is what the cashier just scanned,
    // and the list below is now filtered by it.
    setScanning(false);
    setQuery(code);
    setMessage(null);
    setError(
      scan.status === 'unknown'
        ? `Nothing on this shelf carries ${code}. Search by name, or add the barcode in Catalogue.`
        : `More than one product carries ${code}. Pick the right one from the list.`,
    );
  }

  async function openScanner(): Promise<void> {
    let state: ScannerPermission = await nativeScanner.status();
    if (state === 'undetermined') state = await nativeScanner.request();
    if (state === 'granted') {
      setError(null);
      setScanning(true);
      return;
    }
    // The two refusals are not the same and must not read the same. One is a switch
    // in Settings; the other is hardware that is not there.
    setError(
      state === 'unavailable'
        ? 'No camera available on this device — search by name or SKU instead.'
        : 'Camera access is off for this app. Turn it on in Settings to scan, or search by name.',
    );
  }

  function customerLabel(customer: Customer): string {
    return `${customer.name}${customer.normalizedPhone ? ` · ${customer.normalizedPhone}` : ''}`;
  }

  function chooseCustomer(customer: Customer): void {
    setCustomerId(customer.id);
    setCustomerName(customerLabel(customer));
    setCustomerOptions([]);
    setError(null);
  }

  const customerLookup = useMutation({
    mutationFn: async (term: string) => (await pharmacyApi.customers.search({ q: term }, { limit: 5 })).items,
    onSuccess: (items) => {
      // One match answers itself; more than one means two people answered the
      // same name or phone, and attaching the first would put one buyer's sale
      // and due balance on the other's account.
      const sole = items.length === 1 ? items[0] : undefined;
      if (sole !== undefined) {
        chooseCustomer(sole);
        return;
      }
      if (items.length === 0) {
        setError('No customer matched');
        return;
      }
      setCustomerOptions([...items]);
      setError(null);
    },
    onError: (cause: unknown) => setError(cause instanceof Error ? cause.message : 'Lookup failed'),
  });

  function lookupCustomer(): void {
    const term = customerTerm.trim();
    if (term === '') return;
    // A fresh search invalidates whatever chooser was open for the old term.
    setCustomerOptions([]);
    customerLookup.mutate(term);
  }

  function clearCart(): void {
    setCart([]);
    setReceived('');
    setDigitalAmount('');
    // The customer goes too. Leaving the selection attached carried the previous
    // buyer into the next sale, so a due tender or a loyalty record landed on
    // whoever happened to be served before -- silently, because the chip still
    // read correctly for the customer who had already left.
    setCustomerId(null);
    setCustomerName(null);
    setCustomerTerm('');
    setCustomerOptions([]);
  }

  const receiptHeader = { organizationName: user?.organizationName ?? '', storeName: user?.storeName ?? '', customerName };
  const split = splitTender(totals.total.amount, received, digitalAmount);

  async function checkout(): Promise<void> {
    if (cart.length === 0 || user === null) return;
    if (!split.readable) {
      // The old code forwarded this field to the server verbatim, so "abc" or
      // "1e9" travelled as the received amount and the API decided what to do
      // with it. Whatever it decided, the counter learned about it too late.
      setError('Enter the tendered amounts as plain numbers, e.g. 250 or 250.50');
      return;
    }
    setBusy(true);
    setError(null);
    setMessage(null);

    // Tender rows, the cash `receivedAmount` and the "at least one payment" rule
    // all come from `@pharmacy/sales`, shared with the web and desktop counters.
    const payments = tenderPayments(split, digitalMethod);
    try {
      // Checked before posting: each of these is a refusal the server makes after
      // the fact, and by then the cart is cleared and the customer has left.
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
      const response = await pharmacyApi.sales.create(body, { idempotencyKey });
      setReceipt(receiptFromSale(response.data, receiptHeader));
      clearCart();
    } catch (cause) {
      const offline = (cause as { code?: string }).code === 'NETWORK_ERROR';
      if (offline) {
        const context = envelopeContext(user);
        if (context === null) {
          // No device row means `/sync/events` would answer DEVICE_CONTEXT_REQUIRED,
          // so queueing would only hide the sale in a place it can never leave.
          setError('Offline, and this phone is not registered for offline sales yet. Sign in again once there is signal; keep the cart open and write this sale down.');
          return;
        }
        try {
          await queueSale(body, context);
          // The customer still gets a slip. It carries no receipt number, because
          // that number is the server's to assign and one invented here would
          // later belong to a different sale.
          setReceipt(provisionalReceipt({ ...receiptHeader, issuedAt: new Date().toISOString(), lines: saleLines, payments }));
          setMessage('Offline — sale queued in the local outbox.');
          clearCart();
        } catch (writeFailure) {
          // The outbox write failed, so nothing is recorded anywhere. Previously
          // this threw out of `checkout` into an unhandled rejection while the
          // `finally` cleared the cart regardless -- the sale disappeared without
          // a message. The cart is now the surviving record and it stays up.
          setError(`Offline and the sale could not be saved on this phone (${writeFailure instanceof Error ? writeFailure.message : String(writeFailure)}). Keep the cart open and write it down.`);
        }
      } else {
        // Rejected by the server: nothing was recorded here or there, so the cart
        // is the only evidence of what was scanned and it must survive.
        setError(cause instanceof Error ? cause.message : 'Checkout failed');
      }
    } finally {
      bumpQueueTick();
      setBusy(false);
    }
  }

  async function upload(): Promise<void> {
    setBusy(true);
    setError(null);
    setMessage(null);
    try {
      // Through the shared gate: a manual press while the background timer is
      // mid-flush joins that attempt instead of posting a second batch.
      const result = await flushNow();
      if (result === null) {
        setMessage('An upload is already running.');
        return;
      }
      const accepted = result.uploaded + result.duplicates;
      setMessage(accepted > 0 ? `Uploaded ${accepted} queued sale(s).` : 'Nothing uploaded.');
      if (result.firstError !== null) setError(result.firstError);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Upload failed');
    } finally {
      bumpQueueTick();
      setBusy(false);
    }
  }

  return (
    <View style={{ flex: 1, padding: 16, gap: 12, backgroundColor: '#F8FAFC' }}>
      <View style={{ flexDirection: 'row', alignItems: 'center', gap: 12 }}>
        <TextInput
          placeholder="Scan or search name, SKU…"
          value={query}
          onChangeText={setQuery}
          onSubmitEditing={() => submit(query)}
          style={[input, { flex: 1 }]}
        />
        {query !== '' && (
          <Button
            title="✕"
            accessibilityLabel="Clear search"
            onPress={() => {
              setQuery('');
            }}
          />
        )}
        <Button title="Scan" onPress={() => void openScanner()} />
        <Link href="/(pos)/sync" style={{ color: pending > 0 ? '#A16207' : '#0F766E' }}>
          {pending > 0 ? `${pending} queued` : 'Sync ✓'}
        </Link>
      </View>

      {/* Said before the first tap, not after the sale. A cashier quoting from a
          three-day-old price list should know that is what they are reading. */}
      {stale !== null && <Text style={{ color: '#A16207' }}>{stale}</Text>}

      {/* Announced for screen readers as the list changes, matching the web and
          desktop counters' live regions. */}
      <Text accessibilityLiveRegion="polite" style={{ color: '#64748B', fontSize: 12 }}>
        {searching
          ? matches.length === 0
            ? 'No medicines match.'
            : `${matches.length} medicine${matches.length === 1 ? '' : 's'} match.`
          : ''}
      </Text>
      {searching && medicineMatchesAreFuzzy(matches) && (
        <Text style={{ color: '#A16207', fontSize: 12 }}>No exact match—showing closest medicines.</Text>
      )}

      <FlatList
        data={listData}
        keyExtractor={(item) =>
          item.kind === 'row' ? `row-${item.product.id}` : `${item.kind}-${item.label}`
        }
        style={{ flex: 1 }}
        renderItem={({ item }) => {
          if (item.kind === 'manufacturer' || item.kind === 'dosage') {
            return (
              <Text
                style={{
                  color: '#64748B',
                  fontSize: item.kind === 'manufacturer' ? 13 : 12,
                  fontWeight: item.kind === 'manufacturer' ? '700' : '500',
                  marginTop: 8,
                  marginBottom: 4,
                  paddingLeft: item.kind === 'dosage' ? 8 : 0,
                }}
              >
                {item.label} ({item.count})
              </Text>
            );
          }
          const { product, row } = item;
          const meta = [product.genericName, product.strength, product.dosageForm, product.sku, product.rack]
            .filter((part): part is string => Boolean(part))
            .join(' · ');
          const labelled = row !== null && !(row.matchQuality === 'exact' && row.matchedField === 'name');
          return (
            <Pressable
              onPress={() => addToCart(product)}
              onLongPress={() => {
                // The swap question needs the gesture to stay out of the way of
                // the tap that sells: long-press asks "what else is there".
                if ((product.genericName ?? '').trim() !== '') setAltFor(product);
              }}
              accessibilityHint="Long-press for alternative brands of the same generic"
              style={{ flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', padding: 12, borderRadius: 8, backgroundColor: '#FFFFFF', marginBottom: 4 }}
            >
              {/* Name first, identity under it. A list of bare SKUs is a list
                  picked from memory, which is how the wrong strength gets sold. */}
              <View style={{ flex: 1 }}>
                <Text>
                  {row === null
                    ? product.name
                    : highlightMedicineSpans(product.name, query).map((span, position) =>
                        span.hit ? (
                          <Text key={position} style={{ fontWeight: '700', textDecorationLine: 'underline' }}>
                            {span.text}
                          </Text>
                        ) : (
                          <Text key={position}>{span.text}</Text>
                        ),
                      )}
                </Text>
                <Text style={{ color: '#64748B', fontSize: 12 }}>
                  {meta}
                  {labelled && row !== null && (
                    <Text style={{ color: '#A16207' }}>{` · ${describeMedicineMatch(row)}`}</Text>
                  )}
                </Text>
              </View>
              <Text style={{ color: '#64748B' }}>৳{product.salePrice}</Text>
            </Pressable>
          );
        }}
        ListEmptyComponent={
          <Text style={{ color: '#64748B' }}>
            {products.length === 0 ? 'No shelf on this phone yet — connect once to load it.' : 'No products match.'}
          </Text>
        }
      />

      <View style={{ borderTopWidth: 1, borderColor: '#CBD5E1', paddingTop: 12, gap: 8 }}>
        {cart.map((line) => (
          <View key={line.storeProductId} style={{ flexDirection: 'row', justifyContent: 'space-between' }}>
            <Text>
              {line.name} × {line.quantity}
            </Text>
            <Text>৳{multiply(money(line.unitPrice), line.quantity).amount}</Text>
          </View>
        ))}
        <View style={{ flexDirection: 'row', justifyContent: 'space-between' }}>
          <Text style={{ fontWeight: '700' }}>Total</Text>
          <Text style={{ fontWeight: '700' }}>৳{totals.total.amount}</Text>
        </View>

        <View style={{ flexDirection: 'row', gap: 8 }}>
          <TextInput
            placeholder="Customer name or phone"
            value={customerTerm}
            onChangeText={(value) => {
              setCustomerTerm(value);
              // Editing the term retires the previous result list: it answers a
              // question nobody is asking any more.
              setCustomerOptions([]);
            }}
            style={[input, { flex: 1 }]}
            onSubmitEditing={lookupCustomer}
          />
          {customerId !== null && (
            <Button
              title={customerName ?? 'Attached ✕'}
              onPress={() => {
                setCustomerId(null);
                setCustomerName(null);
              }}
            />
          )}
        </View>

        {customerOptions.length > 0 && (
          <View style={{ gap: 4 }}>
            <Text style={{ color: '#64748B', fontSize: 12 }}>More than one customer matched — attach one:</Text>
            {customerOptions.map((option) => (
              <Pressable
                key={option.id}
                onPress={() => chooseCustomer(option)}
                style={{ flexDirection: 'row', justifyContent: 'space-between', padding: 12, borderRadius: 8, borderWidth: 1, borderColor: '#CBD5E1', backgroundColor: '#FFFFFF' }}
              >
                <Text>{option.name}</Text>
                {/* The normalized phone is what tells two "Md. Rahman" rows apart
                    at a counter where several customers share a name. */}
                <Text style={{ color: '#64748B' }}>{option.normalizedPhone ?? ''}</Text>
              </Pressable>
            ))}
          </View>
        )}

        <TextInput placeholder="Cash received (blank = exact)" value={received} onChangeText={setReceived} inputMode="decimal" style={input} />
        <View style={{ flexDirection: 'row', gap: 8, alignItems: 'center' }}>
          <TextInput placeholder={`${digitalMethod} amount`} value={digitalAmount} onChangeText={setDigitalAmount} inputMode="decimal" style={[input, { flex: 1 }]} />
          {(['bkash', 'nagad'] as const).map((method) => (
            <Pressable
              key={method}
              onPress={() => setDigitalMethod(method)}
              style={{ paddingHorizontal: 12, paddingVertical: 10, borderRadius: 8, backgroundColor: digitalMethod === method ? '#0F766E' : '#FFFFFF', borderWidth: 1, borderColor: '#CBD5E1' }}
            >
              <Text style={{ color: digitalMethod === method ? '#FFFFFF' : '#172033' }}>{method}</Text>
            </Pressable>
          ))}
        </View>
        {!split.readable && <Text style={{ color: '#B91C1C' }}>One of the tendered amounts is not a number.</Text>}
        <View style={{ flexDirection: 'row', justifyContent: 'space-between' }}>
          <Text style={{ color: '#64748B' }}>Cash ৳{split.cash} · {digitalMethod} ৳{split.digital}</Text>
          <Text style={{ color: split.due === '0.00' ? '#64748B' : '#A16207' }}>Due ৳{split.due}</Text>
        </View>
        {split.change !== '0.00' && (
          <View style={{ flexDirection: 'row', justifyContent: 'space-between' }}>
            <Text style={{ fontWeight: '700' }}>Change due</Text>
            <Text style={{ fontWeight: '700' }}>৳{split.change}</Text>
          </View>
        )}

        {busy ? <ActivityIndicator /> : <Button title="Complete sale" onPress={() => void checkout()} disabled={cart.length === 0} />}
        {pending > 0 && <Button title={`Upload ${pending} queued sale(s)`} onPress={() => void upload()} />}

        {message !== null && <Text style={{ color: '#A16207' }}>{message}</Text>}
        {error !== null && <Text style={{ color: '#B91C1C' }}>{error}</Text>}

        {receipt !== null && (
          <View style={{ padding: 12, borderRadius: 8, backgroundColor: '#FFFFFF', gap: 4 }}>
            <Text style={{ fontWeight: '700' }}>
              {receipt.receiptNumber === null ? 'Receipt pending upload' : `Receipt ${receipt.receiptNumber}`}
            </Text>
            {receipt.receiptNumber === null && (
              // Said plainly: the number is the server's to assign, and one invented
              // on this phone would later belong to a different sale.
              <Text style={{ color: '#A16207' }}>Queued on this phone. Its number is issued when it uploads.</Text>
            )}
            {receipt.lines.map((line, index) => (
              <Text key={`${line.name}-${index}`} style={{ color: '#64748B' }}>
                {line.name} × {line.quantity} = ৳{line.lineTotal.amount}
              </Text>
            ))}
            <Text>Total ৳{receipt.totals.total.amount}{receipt.change.amount === '0.00' ? '' : ` · change ৳${receipt.change.amount}`}</Text>
            {/* No printer on a phone, so the slip is shareable as text -- the same
                string the desktop till sends to `hardware.printReceipt`. */}
            <Button title="Share slip" onPress={() => void Share.share({ message: formatReceiptText(receipt) })} />
          </View>
        )}
      </View>

      {scanning && (
        // Over the counter rather than on a route of its own: the cart total stays
        // visible while scanning, and dismissing the camera cannot lose the basket
        // to a back-navigation.
        <View style={{ position: 'absolute', top: 0, left: 0, right: 0, bottom: 0, backgroundColor: '#000000' }}>
          <CameraView
            style={{ flex: 1 }}
            barcodeScannerSettings={{ barcodeTypes: [...scannerFormats] }}
            onBarcodeScanned={({ data }) => onScanned(data)}
          />
          <View style={{ padding: 16, gap: 8 }}>
            <Text style={{ color: '#FFFFFF' }}>
              {cart.length} line(s) · ৳{totals.total.amount}
            </Text>
            {message !== null && <Text style={{ color: '#FFFFFF' }}>{message}</Text>}
            <Button title="Done" onPress={() => setScanning(false)} />
          </View>
        </View>
      )}

      {altFor !== null && (
        <AlternativesModal
          products={products}
          target={altFor}
          onAdd={(alternative) => {
            addToCart(alternative);
            setAltFor(null);
          }}
          onClose={() => setAltFor(null)}
        />
      )}
    </View>
  );
}

/**
 * The alternatives sheet: this shelf's brands of the same generic first, tap to
 * ring one up, then brands the shared catalogue carries that this branch does
 * not stock, read-only.
 *
 * The shelf section is local computation and answers offline -- the phone is
 * the counter most likely to be off-network. The catalogue section is one
 * best-effort query (`retry: false`): a failure renders nothing rather than an
 * error, because the shelf answer is already on screen and mid-sale is no time
 * to report that a reference list is unreachable.
 */
function AlternativesModal({
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
    <Modal visible animationType="slide" onRequestClose={onClose}>
      <View style={{ flex: 1, padding: 16, gap: 8, backgroundColor: '#F8FAFC', paddingTop: 48 }}>
        <Text style={{ fontWeight: '700' }}>Alternatives to {target.name}</Text>
        <Text style={{ color: '#64748B' }}>Same generic: {generic}</Text>
        <ScrollView style={{ flex: 1 }} contentContainerStyle={{ gap: 4 }}>
          <Text style={{ color: '#64748B', fontSize: 12, fontWeight: '700' }}>On this shelf</Text>
          {shelfAlternatives.length === 0 && (
            <Text style={{ color: '#64748B' }}>No other brand of this generic on this shelf.</Text>
          )}
          {shelfAlternatives.map((alt) => (
            <Pressable
              key={alt.item.id}
              onPress={() => onAdd(alt.item)}
              style={{ flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', padding: 12, borderRadius: 8, backgroundColor: '#FFFFFF' }}
            >
              <View style={{ flex: 1 }}>
                <Text>{alt.item.name}</Text>
                <Text style={{ color: '#64748B', fontSize: 12 }}>
                  {[alt.item.strength, alt.item.manufacturer].filter((part): part is string => Boolean(part)).join(' · ')}
                </Text>
              </View>
              {/* The strength warning is the whole point of the sheet: a different
                  strength is a conversation with the customer, not a swap. */}
              <Text style={{ color: alt.sameStrength ? '#64748B' : '#A16207', fontSize: 12 }}>
                {alt.sameStrength ? 'same strength' : 'different strength'} · ৳{alt.item.salePrice}
              </Text>
            </Pressable>
          ))}
          {otherBrands.length > 0 && (
            <View style={{ gap: 4, marginTop: 8 }}>
              <Text style={{ color: '#64748B', fontSize: 12, fontWeight: '700' }}>
                Other brands this branch does not stock
              </Text>
              {otherBrands.map((brand: CatalogAlternativeItem) => (
                <View key={brand.catalogProductId} style={{ padding: 12, borderRadius: 8, backgroundColor: '#FFFFFF' }}>
                  <Text>{brand.name}</Text>
                  <Text style={{ color: '#64748B', fontSize: 12 }}>
                    {[brand.strength, brand.manufacturer].filter((part): part is string => Boolean(part)).join(' · ')}
                    {!brand.sameStrength ? ' · different strength' : ''}
                  </Text>
                </View>
              ))}
            </View>
          )}
        </ScrollView>
        <Button title="Close" onPress={onClose} />
      </View>
    </Modal>
  );
}

const input = {
  padding: 12,
  borderRadius: 8,
  borderWidth: 1,
  borderColor: '#CBD5E1',
  backgroundColor: '#FFFFFF',
  color: '#172033',
} as const;

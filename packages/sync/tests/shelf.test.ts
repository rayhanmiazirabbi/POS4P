import { describe, expect, it, vi } from 'vitest';

import {
  createShelfStore,
  describeShelfAge,
  loadShelf,
  matchShelf,
  readShelf,
  scanShelf,
  submitShelfEntry,
  toShelfProduct,
  type OutboxStorage,
  type ShelfCache,
  type ShelfProduct,
  type ShelfSource,
} from '../src/index';

const mirpur = 'store-mirpur';
const uttara = 'store-uttara';

/** A `ShelfItem`-shaped row as `GET /products/current` returns it. */
function row(sku: string, salePrice: string, rack: string | null = null): ShelfSource {
  return { id: `p-${sku}`, sku, name: `Medicine ${sku}`, barcode: null, salePrice, rack };
}

function memoryStorage(initial: string | null = null): OutboxStorage & { blob: () => string | null } {
  let blob = initial;
  return {
    async read() {
      return blob;
    },
    async write(value) {
      blob = value;
    },
    blob: () => blob,
  };
}

describe('readShelf', () => {
  const cache: ShelfCache = {
    storeId: mirpur,
    fetchedAt: '2026-01-01T09:00:00.000Z',
    products: [
      { id: 'p-1', sku: 'PARA-500', name: 'Paracetamol 500mg', barcode: '8901234567890', salePrice: '12.00', rack: 'A1' },
    ],
  };

  it('reports an empty shelf when nothing has been cached', () => {
    expect(readShelf(null, mirpur, '2026-01-01T09:00:00.000Z')).toEqual({ status: 'empty' });
  });

  it('serves the cached shelf with its age when the branch matches', () => {
    const read = readShelf(cache, mirpur, '2026-01-01T09:30:00.000Z');
    expect(read.status).toBe('cached');
    expect(read.status === 'cached' && read.ageMs).toBe(30 * 60 * 1000);
    expect(read.status === 'cached' && read.products).toEqual(cache.products);
  });

  it('refuses a shelf cached for another branch instead of filtering it', () => {
    // The same SKU exists in both branches at different prices, so serving Mirpur's
    // cache to an Uttara till is a wrong price on a printed receipt -- and there is
    // no field on the row that would let the till notice.
    const read = readShelf(cache, uttara, '2026-01-01T09:00:00.000Z');
    expect(read).toEqual({ status: 'other-branch', cachedStoreId: mirpur });
  });

  it('never reports a shelf from the future when the clock moves backwards', () => {
    // Phones correct their clocks by NTP mid-shift. A negative age would render as
    // "-4h old", or worse, pass a `age < maxAge` freshness check for ever.
    const read = readShelf(cache, mirpur, '2026-01-01T05:00:00.000Z');
    expect(read.status === 'cached' && read.ageMs).toBe(0);
  });

  it('reports a zero age rather than NaN when the stamp is unparseable', () => {
    const read = readShelf({ ...cache, fetchedAt: 'not a date' }, mirpur, '2026-01-01T09:00:00.000Z');
    expect(read.status === 'cached' && read.ageMs).toBe(0);
  });
});

describe('toShelfProduct', () => {
  it('keeps what a cart line needs and normalises an absent rack and barcode', () => {
    // Both are `string | undefined | null` on the API row; the cache stores one
    // shape so the JSON round-trip cannot turn an absent key into a present one,
    // and `matchShelf` can compare `barcode` without re-checking for undefined.
    expect(toShelfProduct({ id: 'p-1', sku: 'PARA-500', name: 'Paracetamol 500mg', salePrice: '12.00' })).toEqual({
      id: 'p-1',
      sku: 'PARA-500',
      name: 'Paracetamol 500mg',
      barcode: null,
      salePrice: '12.00',
      rack: null,
    });
  });
});

describe('createShelfStore', () => {
  it('serves a shelf saved in an earlier session', async () => {
    // The whole point. All three shells held this in `useState`, so a device that
    // started up with no signal showed an empty shelf, nothing could enter a cart,
    // and the offline outbox beneath it never received a sale.
    const storage = memoryStorage();
    await createShelfStore(storage).save(mirpur, [row('PARA-500', '12.00')], '2026-01-01T09:00:00.000Z');

    const reopened = await createShelfStore(storage).read(mirpur, '2026-01-02T09:00:00.000Z');
    expect(reopened.status).toBe('cached');
    expect(reopened.status === 'cached' && reopened.products.map((product) => product.sku)).toEqual(['PARA-500']);
    // A day old and still served: a stale price beats a refused sale.
    expect(reopened.status === 'cached' && reopened.ageMs).toBe(24 * 60 * 60 * 1000);
  });

  it('replaces the shelf rather than merging into it', async () => {
    // A product withdrawn from sale disappears from `/products/current`. Merging
    // would keep it sellable on the device indefinitely.
    const store = createShelfStore(memoryStorage());
    await store.save(mirpur, [row('PARA-500', '12.00'), row('OMEP-20', '30.00')]);
    await store.save(mirpur, [row('PARA-500', '14.00')]);

    const read = await store.read(mirpur);
    expect(read.status === 'cached' && read.products).toEqual([
      { id: 'p-PARA-500', sku: 'PARA-500', name: 'Medicine PARA-500', barcode: null, salePrice: '14.00', rack: null },
    ]);
  });

  it('reads a corrupt blob as empty instead of throwing', async () => {
    // The opposite of the outbox, which throws on an unreadable blob because
    // discarding paid-for sales silently is worse than an error. Nothing here is
    // absent from the server, so the cost of dropping it is one fetch -- while
    // throwing would take down the counter on the mount that needed the cache.
    expect(await createShelfStore(memoryStorage('{not json')).read(mirpur)).toEqual({ status: 'empty' });
  });

  it('reads a well-formed blob with the wrong shape as empty', async () => {
    const storage = memoryStorage(JSON.stringify({ storeId: mirpur, fetchedAt: '2026-01-01T09:00:00.000Z', products: [{ sku: 'PARA-500' }] }));
    expect(await createShelfStore(storage).read(mirpur)).toEqual({ status: 'empty' });
  });

  it('discards a shelf cached before rows carried a name', async () => {
    // Devices in the field hold shelves written when a row was `{id, sku, salePrice,
    // rack}`, and `name` is what the cashier now reads off the list. Serving those
    // rows would put blank labels on the screen, so they read as empty and cost one
    // refetch -- the same answer as any other blob this version cannot use.
    const storage = memoryStorage(
      JSON.stringify({
        storeId: mirpur,
        fetchedAt: '2026-01-01T09:00:00.000Z',
        products: [{ id: 'p-1', sku: 'PARA-500', salePrice: '12.00', rack: 'A1' }],
      }),
    );
    expect(await createShelfStore(storage).read(mirpur)).toEqual({ status: 'empty' });
  });

  it('reads as empty when the storage port itself fails', async () => {
    const storage: OutboxStorage = {
      async read() {
        throw new Error('keychain locked');
      },
      async write() {},
    };
    expect(await createShelfStore(storage).read(mirpur)).toEqual({ status: 'empty' });
  });

  it('still returns the shelf when it could not be written', async () => {
    // The caller has the products in hand and is about to sell from them. Failing
    // here would break a working counter to report a degraded one.
    const storage: OutboxStorage = {
      async read() {
        return null;
      },
      async write() {
        throw new Error('disk full');
      },
    };
    const cache = await createShelfStore(storage).save(mirpur, [row('PARA-500', '12.00')]);
    expect(cache.products).toHaveLength(1);
    expect(cache.storeId).toBe(mirpur);
  });

  it('refuses the previous branch shelf after a till changes branch', async () => {
    const storage = memoryStorage();
    const store = createShelfStore(storage);
    await store.save(mirpur, [row('PARA-500', '12.00')]);
    expect(await store.read(uttara)).toEqual({ status: 'other-branch', cachedStoreId: mirpur });
  });

  it('reads as empty once cleared', async () => {
    const storage = memoryStorage();
    const store = createShelfStore(storage);
    await store.save(mirpur, [row('PARA-500', '12.00')]);
    await store.clear();
    // Empty, not `other-branch`: a signed-out device holds no branch's shelf, and
    // reporting one would put a former branch's name on the next screen.
    expect(await store.read(mirpur)).toEqual({ status: 'empty' });
  });
});

describe('describeShelfAge', () => {
  it('speaks in units a cashier can act on', () => {
    expect(describeShelfAge(0)).toBe('just now');
    expect(describeShelfAge(59_000)).toBe('just now');
    expect(describeShelfAge(90_000)).toBe('1 min ago');
    expect(describeShelfAge(3 * 60 * 60 * 1000)).toBe('3 h ago');
    expect(describeShelfAge(30 * 60 * 60 * 1000)).toBe('yesterday');
    expect(describeShelfAge(5 * 24 * 60 * 60 * 1000)).toBe('5 days ago');
  });
});

describe('loadShelf', () => {
  it('serves the cache before the network is asked', async () => {
    // The reason `onCached` exists. A device with no signal waits out a connection
    // timeout before the fetch rejects; painting the cached shelf only afterwards
    // means the counter stares at an empty list for as long as that takes.
    const storage = memoryStorage();
    await createShelfStore(storage).save(mirpur, [row('PARA-500', '12.00')]);
    const order: string[] = [];
    const seen = vi.fn(() => {
      order.push('cached');
    });

    await loadShelf(
      createShelfStore(storage),
      mirpur,
      async () => {
        order.push('fetched');
        return [row('PARA-500', '14.00')];
      },
      seen,
    );

    expect(order).toEqual(['cached', 'fetched']);
    expect(seen).toHaveBeenCalledTimes(1);
  });

  it('keeps selling from the cache when the fetch fails', async () => {
    // The whole point of the module. Each shell used to wrap this call in
    // `.catch(() => setError(...))`, so losing signal reported "Could not load the
    // shelf" and emptied a list it could have filled -- at exactly the moment the
    // offline outbox beneath it was meant to take over.
    const storage = memoryStorage();
    await createShelfStore(storage).save(mirpur, [row('PARA-500', '12.00')], '2026-01-01T09:00:00.000Z');

    const load = await loadShelf(createShelfStore(storage), mirpur, () => Promise.reject(new Error('Network request failed')));

    expect(load.status).toBe('stale');
    expect(load.status === 'stale' && load.products.map((product) => product.sku)).toEqual(['PARA-500']);
    expect(load.status === 'stale' && load.note).toMatch(/prices last updated/);
  });

  it('reports the fetch failure when there is no cache to fall back on', async () => {
    // First run on a device that has never had signal. Nothing to sell from, and
    // the network error is the actionable half of that.
    const load = await loadShelf(createShelfStore(memoryStorage()), mirpur, () => Promise.reject(new Error('Network request failed')));
    expect(load).toEqual({ status: 'unavailable', reason: 'Network request failed' });
  });

  it('refuses to fall back to another branch cache', async () => {
    // A shelf at the wrong branch's prices is worse than no shelf: it looks right,
    // it rings up, and the receipt is wrong.
    const storage = memoryStorage();
    await createShelfStore(storage).save(mirpur, [row('PARA-500', '12.00')]);

    const seen = vi.fn();
    const load = await loadShelf(createShelfStore(storage), uttara, () => Promise.reject(new Error('offline')), seen);

    expect(load).toEqual({ status: 'unavailable', reason: 'offline' });
    expect(seen).not.toHaveBeenCalled();
  });

  it('saves what it fetched so the next start has a shelf', async () => {
    const storage = memoryStorage();
    const load = await loadShelf(createShelfStore(storage), mirpur, async () => [row('PARA-500', '12.00')]);

    expect(load.status).toBe('fresh');
    expect(await createShelfStore(storage).read(mirpur)).toMatchObject({ status: 'cached' });
  });

  it('serves the fetched shelf even when it could not be written', async () => {
    // A read-only or full disk degrades the next start, not this sale.
    const storage: OutboxStorage = {
      async read() {
        return null;
      },
      async write() {
        throw new Error('quota exceeded');
      },
    };
    const load = await loadShelf(createShelfStore(storage), mirpur, async () => [row('PARA-500', '12.00')]);
    expect(load.status === 'fresh' && load.products).toHaveLength(1);
  });
});

/** A shelf with two strengths of one medicine, which is where scanning earns its keep. */
const counter: readonly ShelfProduct[] = [
  toShelfProduct({ id: 'p-1', sku: 'PARA-500', name: 'Paracetamol 500mg', salePrice: '12.00', barcode: '8901234567890' }),
  toShelfProduct({ id: 'p-2', sku: 'PARA-650', name: 'Paracetamol 650mg', salePrice: '15.00', barcode: '8901234567891' }),
  toShelfProduct({ id: 'p-3', sku: 'OMEP-20', name: 'Omeprazole 20mg', salePrice: '30.00' }),
];

describe('matchShelf', () => {
  it('matches nothing for a blank query', () => {
    // A cleared search box shows the whole shelf, which is the caller's job. An
    // empty needle here would substring-match every row and say it meant it.
    expect(matchShelf(counter, '   ')).toEqual([]);
  });

  it('puts a barcode hit first, ahead of anything typed', () => {
    const [first] = matchShelf(counter, '8901234567891');
    expect(first?.matchedBy).toBe('barcode');
    expect(first?.product.sku).toBe('PARA-650');
  });

  it('matches a scanner that pads the code with whitespace', () => {
    // Barcode guns append a terminator and some emit spaces inside the digits.
    // Normalising both sides is why the desktop till needs no scanner driver: the
    // gun types into the search box and this resolves what it typed.
    expect(matchShelf(counter, ' 8901 234 567890 \n')[0]?.product.sku).toBe('PARA-500');
  });

  it('ranks an exact SKU above a substring of one', () => {
    const kinds = matchShelf(counter, 'PARA-500').map((match) => match.matchedBy);
    expect(kinds[0]).toBe('sku');
  });

  it('finds a product by name, case-insensitively', () => {
    // The reason `name` was added to the shelf endpoint. A cashier asked for
    // "omeprazole" cannot find `OMEP-20` by typing what the customer said.
    expect(matchShelf(counter, 'omeprazole').map((match) => match.product.sku)).toEqual(['OMEP-20']);
  });

  it('returns both strengths when the name is shared, in a stable order', () => {
    // Deliberately not narrowed to one. Picking the top guess for the cashier is
    // how 650mg gets sold as 500mg; the screen shows both and a person chooses.
    expect(matchShelf(counter, 'Paracetamol').map((match) => match.product.sku)).toEqual(['PARA-500', 'PARA-650']);
  });

  it('never matches a barcodeless row by its absent code', () => {
    // `OMEP-20` has no barcode. A null compared against a normalised query must not
    // collapse to equal, or every scan of an unknown code would ring it up.
    expect(matchShelf(counter, '9999999999999')).toEqual([]);
  });
});

describe('scanShelf', () => {
  it('resolves a single barcode hit to a product the counter may add directly', () => {
    const scan = scanShelf(counter, '8901234567890');
    expect(scan.status).toBe('product');
    expect(scan.status === 'product' && scan.product.sku).toBe('PARA-500');
  });

  it('reports an unrecognised code without treating it as an error', () => {
    // Ordinary: a line that was never barcoded, or a code from another catalogue.
    // The screen offers the search box; it does not show a failure.
    expect(scanShelf(counter, '0000000000000')).toEqual({ status: 'unknown', scanned: '0000000000000' });
  });

  it('refuses to guess when two rows share a barcode', async () => {
    const twins = [
      ...counter,
      toShelfProduct({ id: 'p-4', sku: 'PARA-500-DUP', name: 'Paracetamol 500mg (import)', salePrice: '13.50', barcode: '8901234567890' }),
    ];
    const scan = scanShelf(twins, '8901234567890');
    expect(scan.status).toBe('ambiguous');
    expect(scan.status === 'ambiguous' && scan.options).toHaveLength(2);
  });

  it('does not add a product the code only matched by name', () => {
    // The important line. A camera hands over a code with nobody watching, so only
    // an identifier match is certain -- a code that happens to appear inside a name
    // is a coincidence, and acting on it sells a product nobody scanned.
    const scan = scanShelf([toShelfProduct({ id: 'p-9', sku: 'VIT-C-500', name: 'Vitamin C 500', salePrice: '9.00' })], '500');
    expect(scan.status).toBe('ambiguous');
  });

  it('reports a scan of nothing as unknown', () => {
    // A camera frame that decoded to whitespace, or an empty submitted search box.
    expect(scanShelf(counter, '  ')).toEqual({ status: 'unknown', scanned: '' });
  });
});

describe('submitShelfEntry', () => {
  it('adds an exact SKU the cashier typed in full', () => {
    // The keyboard till's entire workflow: type the SKU, press Enter, next customer.
    const entry = submitShelfEntry(counter, 'omep-20');
    expect(entry.status === 'product' && entry.product.sku).toBe('OMEP-20');
  });

  it('still prefers a barcode when a gun typed one into the box', () => {
    const entry = submitShelfEntry(counter, '8901234567891');
    expect(entry.status === 'product' && entry.product.sku).toBe('PARA-650');
  });

  it('refuses to pick between two strengths for a partial name', () => {
    // The regression this exists to prevent. The desktop till added `matches[0]` on
    // Enter, which was survivable while the search read SKUs only and became a
    // hazard the moment it read names: "para" would have sold whichever of 500mg
    // and 650mg happened to sort first.
    const entry = submitShelfEntry(counter, 'para');
    expect(entry.status).toBe('ambiguous');
    expect(entry.status === 'ambiguous' && entry.options).toHaveLength(2);
  });

  it('refuses a partial SKU even when it matches exactly one product', () => {
    // One match today is two after the next product is added, and a cashier who
    // learned that "ome" rings up Omeprazole will keep pressing Enter afterwards.
    expect(submitShelfEntry(counter, 'ome').status).toBe('ambiguous');
  });

  it('reports an entry that matches nothing', () => {
    expect(submitShelfEntry(counter, 'ZZZ-1')).toEqual({ status: 'unknown', scanned: 'ZZZ-1' });
  });
});

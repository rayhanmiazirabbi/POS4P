'use client';

import { useQuery, useQueryClient } from '@tanstack/react-query';
import type { PharmacyProduct, ShelfItem } from '@pharmacy/api';
import { colors, spacing, tokens } from '@pharmacy/design-tokens';
import { useState, type CSSProperties, type ReactNode } from 'react';
import { z } from 'zod';

import { pharmacyApi } from '@/lib/api';
import { decimalAmount, fieldIssue } from '@/lib/validation';

const card: CSSProperties = { background: colors.surface, border: `1px solid ${colors.border}`, borderRadius: 12, padding: spacing.lg };
const input: CSSProperties = { padding: spacing.sm, borderRadius: 8, border: `1px solid ${colors.border}` };
const button: CSSProperties = { ...input, cursor: 'pointer', background: colors.primary, color: colors.primaryForeground, border: 'none', fontWeight: tokens.typography.weights.medium };

/** A product must be creatable with a name; a shelf row needs a SKU and a price
 *  the till can charge. Both are checked here because a refusal from the server
 *  arrives after the operator has moved on to the next row. */
const newProductSchema = z.object({
  name: z.string().trim().min(1, 'A product needs a name'),
  unit: z.string().trim().min(1, 'Enter a unit, e.g. box'),
});

const enableShelfSchema = z.object({
  sku: z.string().trim().min(1, 'A shelf row needs a SKU'),
  price: decimalAmount,
});

export default function CataloguePage(): ReactNode {
  const queryClient = useQueryClient();
  const [name, setName] = useState('');
  const [unit, setUnit] = useState('box');
  const [barcode, setBarcode] = useState('');
  const [sku, setSku] = useState('');
  const [price, setPrice] = useState('');
  const [selected, setSelected] = useState<string>('');
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);

  const productsQuery = useQuery({
    queryKey: ['catalogue', 'products'],
    queryFn: async () => (await pharmacyApi.products.listPharmacyProducts({ limit: 100 })).items,
    staleTime: 60_000,
  });
  const shelfQuery = useQuery({
    queryKey: ['catalogue', 'shelf'],
    queryFn: async () => (await pharmacyApi.products.listCurrentStoreProducts({ includeInactive: true })).items,
    staleTime: 30_000,
  });

  const products = productsQuery.data ?? [];
  const shelf = shelfQuery.data ?? [];

  async function refreshLists(): Promise<void> {
    await queryClient.invalidateQueries({ queryKey: ['catalogue'] });
  }

  // An empty form is not an error, it is a form -- the disabled button says that
  // part. Typed-but-invalid input gets the inline message.
  const productForm = newProductSchema.safeParse({ name, unit });
  const productFormError = productForm.success || name.trim() === '' ? null : fieldIssue(productForm);

  const enableForm = enableShelfSchema.safeParse({ sku, price });
  const priceProblem = enableForm.success || price.trim() === '' ? null : fieldIssue(enableForm);

  async function createProduct(): Promise<void> {
    setError(null);
    setNote(null);
    if (!productForm.success) return;
    try {
      await pharmacyApi.products.createPharmacyProduct(
        barcode.trim() === '' ? { name: name.trim(), unit: unit.trim() } : { name: name.trim(), unit: unit.trim(), barcode: barcode.trim() },
      );
      setName('');
      setBarcode('');
      setNote('Product created.');
      await refreshLists();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Could not create the product');
    }
  }

  async function enableOnShelf(): Promise<void> {
    setError(null);
    setNote(null);
    if (!enableForm.success || selected === '') return;
    try {
      await pharmacyApi.products.enableStoreProduct({ pharmacyProductId: selected, sku: sku.trim(), salePrice: price.trim() });
      setSku('');
      setPrice('');
      setSelected('');
      setNote('Product enabled on this shelf.');
      await refreshLists();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Could not enable the product');
    }
  }

  return (
    <main className="split-grid split-grid--wide">
      <section style={card}>
        <h2 style={{ marginTop: 0, fontSize: tokens.typography.sizes.lg }}>Products ({products.length})</h2>
        {productsQuery.isPending && <p style={{ color: colors.muted }}>Loading…</p>}
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: tokens.typography.sizes.sm }}>
          <thead>
            <tr style={{ textAlign: 'left', color: colors.muted }}>
              <th>Name</th><th>Unit</th><th>Barcode</th><th>Active</th><th />
            </tr>
          </thead>
          <tbody>
            {products.map((product) => (
              <tr key={product.id}>
                <td style={{ padding: `${spacing.xs} 0` }}>{product.name}</td>
                <td>{product.unit}</td>
                <td>{product.barcode ?? '—'}</td>
                <td>{product.active ? 'yes' : 'no'}</td>
                <td>
                  <button type="button" style={{ ...input, cursor: 'pointer' }} onClick={() => setSelected(product.id)}>
                    Shelf
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {!productsQuery.isPending && products.length === 0 && <p style={{ color: colors.muted }}>No products yet.</p>}
        <h3 style={{ marginBottom: spacing.xs }}>New product</h3>
        <div style={{ display: 'flex', gap: spacing.sm, flexWrap: 'wrap' }}>
          <input style={input} placeholder="Name" value={name} onChange={(event) => setName(event.target.value)} />
          <input style={{ ...input, width: 80 }} placeholder="Unit" value={unit} onChange={(event) => setUnit(event.target.value)} />
          <input style={input} placeholder="Barcode (optional)" value={barcode} onChange={(event) => setBarcode(event.target.value)} />
          <button type="button" style={button} disabled={!productForm.success} onClick={() => void createProduct()}>Create</button>
        </div>
        {productFormError !== null && (
          <p role="alert" style={{ margin: `${spacing.xs} 0 0`, color: colors.danger, fontSize: tokens.typography.sizes.sm }}>{productFormError}</p>
        )}
      </section>

      <section style={{ ...card, display: 'flex', flexDirection: 'column', gap: spacing.md }}>
        <h2 style={{ margin: 0, fontSize: tokens.typography.sizes.lg }}>This shelf ({shelf.length})</h2>
        {shelfQuery.isPending && <p style={{ color: colors.muted }}>Loading…</p>}
        <ul style={{ listStyle: 'none', padding: 0, margin: 0, maxHeight: '40vh', overflowY: 'auto' }}>
          {shelf.map((row) => (
            <li key={row.id} style={{ display: 'flex', justifyContent: 'space-between', gap: spacing.sm, marginBottom: spacing.xs }}>
              {/* The `no barcode` flag is honest about what this screen can do: a
                  barcode is attached when a product is created (the New product
                  form), but there is no edit endpoint, so a product already on the
                  shelf without one cannot be fixed here -- re-create it with its code. */}
              <span>
                {row.name} · {row.sku}
                {row.barcode ? '' : ' · no barcode'}
              </span>
              <span>৳{row.salePrice} {row.active ? '' : '· inactive'}</span>
            </li>
          ))}
        </ul>
        <h3 style={{ margin: 0 }}>Enable a product</h3>
        <select style={input} value={selected} onChange={(event) => setSelected(event.target.value)}>
          <option value="">Choose product…</option>
          {products.map((product) => <option key={product.id} value={product.id}>{product.name}</option>)}
        </select>
        <input style={input} placeholder="SKU" value={sku} onChange={(event) => setSku(event.target.value)} />
        <div>
          <input style={{ ...input, width: '100%', boxSizing: 'border-box' }} placeholder="Sale price, e.g. 10.00" value={price} onChange={(event) => setPrice(event.target.value)} inputMode="decimal" />
          {priceProblem !== null && (
            <p role="alert" style={{ margin: `${spacing.xs} 0 0`, color: colors.danger, fontSize: tokens.typography.sizes.sm }}>{priceProblem}</p>
          )}
        </div>
        <button type="button" style={button} disabled={selected === '' || !enableForm.success} onClick={() => void enableOnShelf()}>
          Enable
        </button>
        {(error !== null || note !== null || productsQuery.isError || shelfQuery.isError) && (
          <p role={error !== null ? 'alert' : undefined} style={{ margin: 0, color: error !== null ? colors.danger : colors.success }}>
            {error ??
              note ??
              (productsQuery.isError
                ? productsQuery.error instanceof Error
                  ? productsQuery.error.message
                  : 'Could not load the catalogue'
                : 'Could not load the catalogue')}
          </p>
        )}
      </section>
    </main>
  );
}

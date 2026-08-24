'use client';

import { useQuery } from '@tanstack/react-query';
import type { PublicCatalogueItem } from '@pharmacy/api';
import { colors, spacing, tokens } from '@pharmacy/design-tokens';
import { useParams } from 'next/navigation';
import { useState, type CSSProperties, type ReactNode } from 'react';
import { z } from 'zod';

import { pharmacyApi } from '@/lib/api';
import { fieldIssue } from '@/lib/validation';

const card: CSSProperties = { background: colors.surface, border: `1px solid ${colors.border}`, borderRadius: 12, padding: spacing.lg };
const input: CSSProperties = { padding: spacing.sm, borderRadius: 8, border: `1px solid ${colors.border}` };
const button: CSSProperties = { ...input, cursor: 'pointer', background: colors.primary, color: colors.primaryForeground, border: 'none', fontWeight: tokens.typography.weights.medium };
const quietButton: CSSProperties = { ...input, cursor: 'pointer', background: colors.background };

type Fulfillment = 'pickup' | 'delivery';

/** The server takes decimal strings and refuses zero or negative lines. */
const orderQuantity = z
  .string()
  .trim()
  .regex(/^\d+(\.\d{1,4})?$/, 'Enter a quantity, e.g. 2 or 0.5')
  .refine((value) => Number(value) > 0, { message: 'Quantity must be above zero' });
const checkoutSchema = z
  .object({
    fulfillment: z.enum(['pickup', 'delivery']),
    addressLine: z.string().trim(),
  })
  .refine((form) => form.fulfillment === 'pickup' || form.addressLine.length > 0, {
    path: ['addressLine'],
    message: 'A delivery order needs an address',
  });

export default function StorefrontPage(): ReactNode {
  const params = useParams<{ orgSlug: string; slug: string }>();
  const orgSlug = typeof params.orgSlug === 'string' ? params.orgSlug : '';
  const slug = typeof params.slug === 'string' ? params.slug : '';

  const [cart, setCart] = useState<Record<string, string>>({});
  const [fulfillment, setFulfillment] = useState<Fulfillment>('pickup');
  const [addressLine, setAddressLine] = useState('');
  const [city, setCity] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [placed, setPlaced] = useState<{ id: string; status: string; total: string } | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const catalogueQuery = useQuery({
    queryKey: ['storefront', orgSlug, slug],
    queryFn: async () => (await pharmacyApi.storefront.catalogue(orgSlug, slug)).data,
    staleTime: 30_000,
    enabled: orgSlug !== '' && slug !== '',
  });

  const items: readonly PublicCatalogueItem[] = catalogueQuery.data ?? [];

  function setQuantity(item: PublicCatalogueItem, raw: string): void {
    setError(null);
    const trimmed = raw.trim();
    if (trimmed === '') {
      const next = { ...cart };
      delete next[item.storeProductId];
      setCart(next);
      return;
    }
    const parsed = orderQuantity.safeParse(trimmed);
    if (!parsed.success) {
      setError(parsed.error.issues[0]?.message ?? 'Invalid quantity');
      return;
    }
    setCart({ ...cart, [item.storeProductId]: trimmed });
  }

  function cartLines(): { item: PublicCatalogueItem; quantity: string }[] {
    return items
      .filter((item) => cart[item.storeProductId] !== undefined)
      .map((item) => ({ item, quantity: cart[item.storeProductId] as string }));
  }

  function cartTotal(): string {
    return cartLines()
      .reduce((sum, line) => sum + Number(line.item.price) * Number(line.quantity), 0)
      .toFixed(2);
  }

  async function placeOrder(): Promise<void> {
    setError(null);
    setPlaced(null);
    const lines = cartLines();
    if (lines.length === 0 || submitting) return;
    const form = checkoutSchema.safeParse({ fulfillment, addressLine });
    if (!form.success) {
      setError(fieldIssue(form));
      return;
    }
    setSubmitting(true);
    try {
      const response = await pharmacyApi.storefront.checkout(orgSlug, slug, {
        items: lines.map((line) => ({
          storeProductId: line.item.storeProductId,
          quantity: line.quantity,
        })),
        fulfillment,
        ...(fulfillment === 'delivery'
          ? { deliveryAddress: { addressLine: addressLine.trim(), city: city.trim() } }
          : {}),
      });
      setPlaced({
        id: response.data.id,
        status: response.data.status,
        total: response.data.total,
      });
      setCart({});
      setAddressLine('');
      setCity('');
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Could not place the order');
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <main className="split-grid split-grid--wide">
      <section style={card}>
        <h1 style={{ marginTop: 0, fontSize: tokens.typography.sizes.lg }}>Shop</h1>
        {catalogueQuery.isPending && <p style={{ color: colors.muted }}>Loading…</p>}
        {catalogueQuery.isError && (
          <p role="alert" style={{ color: colors.danger }}>
            This storefront is not available.
          </p>
        )}
        <ul style={{ listStyle: 'none', margin: 0, padding: 0 }}>
          {items.map((item) => (
            <li
              key={item.storeProductId}
              style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: spacing.sm, borderBottom: `1px solid ${colors.border}`, padding: `${spacing.sm} 0` }}
            >
              <span>
                {item.name} · ৳{item.price}
                {item.prescriptionRequired ? ' · prescription needed' : ''}
                {item.pickupEnabled ? '' : ' · no pickup'}
                {item.deliveryEnabled ? '' : ' · no delivery'}
              </span>
              <span style={{ display: 'flex', gap: spacing.xs, alignItems: 'center' }}>
                <input
                  style={{ ...input, width: 72 }}
                  placeholder="Qty"
                  value={cart[item.storeProductId] ?? ''}
                  inputMode="decimal"
                  onChange={(event) => setQuantity(item, event.target.value)}
                />
              </span>
            </li>
          ))}
        </ul>
        {!catalogueQuery.isPending && items.length === 0 && !catalogueQuery.isError && (
          <p style={{ color: colors.muted }}>Nothing is listed yet.</p>
        )}
      </section>

      <section style={{ ...card, display: 'flex', flexDirection: 'column', gap: spacing.md }}>
        <h2 style={{ margin: 0, fontSize: tokens.typography.sizes.lg }}>Your basket</h2>
        <ul style={{ listStyle: 'none', margin: 0, padding: 0 }}>
          {cartLines().map((line) => (
            <li key={line.item.storeProductId} style={{ display: 'flex', justifyContent: 'space-between', marginBottom: spacing.xs }}>
              <span>
                {line.item.name} × {line.quantity}
              </span>
              <span>৳{(Number(line.item.price) * Number(line.quantity)).toFixed(2)}</span>
            </li>
          ))}
        </ul>
        {cartLines().length === 0 && <p style={{ color: colors.muted }}>Pick quantities from the shelf.</p>}

        <div style={{ display: 'flex', gap: spacing.sm }}>
          <button type="button" style={fulfillment === 'pickup' ? button : quietButton} onClick={() => setFulfillment('pickup')}>
            Pickup
          </button>
          <button type="button" style={fulfillment === 'delivery' ? button : quietButton} onClick={() => setFulfillment('delivery')}>
            Delivery
          </button>
        </div>

        {fulfillment === 'delivery' && (
          <>
            <input style={input} placeholder="Address" value={addressLine} onChange={(event) => setAddressLine(event.target.value)} />
            <input style={input} placeholder="City (optional)" value={city} onChange={(event) => setCity(event.target.value)} />
          </>
        )}

        {cartLines().length > 0 && (
          <p style={{ margin: 0, fontWeight: tokens.typography.weights.medium }}>Total: ৳{cartTotal()}</p>
        )}

        <button type="button" style={button} disabled={cartLines().length === 0 || submitting} onClick={() => void placeOrder()}>
          {submitting ? 'Placing…' : 'Place order'}
        </button>

        {placed !== null && (
          <p role="status" style={{ margin: 0, color: colors.success }}>
            Order placed — {placed.status}. Total ৳{placed.total}.
          </p>
        )}
        {error !== null && (
          <p role="alert" style={{ margin: 0, color: colors.danger, fontSize: tokens.typography.sizes.sm }}>
            {error}
          </p>
        )}
      </section>
    </main>
  );
}

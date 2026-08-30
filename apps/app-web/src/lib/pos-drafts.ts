'use client';

import type { DiscountInput } from '@pharmacy/api';
import type { ShelfProduct } from '@pharmacy/sync';
import { create } from 'zustand';
import { z } from 'zod';

import { localRecord } from './database';

export type CartLine = {
  storeProductId: string;
  sku: string;
  name: string;
  unit: string;
  quantity: number;
  unitPrice: string;
  discountMode: DiscountInput['mode'];
  discountValue: string;
  /** Where to physically pick the item; optional because catalogue-adopted
   *  lines carry no rack until the shelf record has one. */
  rack?: string | null | undefined;
  unavailable?: boolean | undefined;
};

export type PosDraft = {
  lines: readonly CartLine[];
  customerId: string | null;
  customerName: string | null;
  globalDiscountMode: DiscountInput['mode'];
  globalDiscountValue: string;
  deliveryCharge: string;
  otherFeeLabel: string;
  otherFee: string;
  advance: string;
  advanceReference: string;
  updatedAt: string;
};

export type HeldCart = {
  id: string;
  heldAt: string;
  label: string;
  draft: PosDraft;
};

export type PersistedPosDraftV1 = {
  version: 1;
  active: PosDraft;
  held: readonly HeldCart[];
};

const cartLineSchema = z.object({
  storeProductId: z.string().min(1),
  sku: z.string(),
  name: z.string().min(1),
  unit: z.string().min(1),
  quantity: z.number().int().positive(),
  unitPrice: z.string().regex(/^\d+(?:\.\d+)?$/),
  discountMode: z.enum(['percentage', 'flat']),
  discountValue: z.string(),
  rack: z.string().nullable().optional(),
  unavailable: z.boolean().optional(),
});

const draftSchema: z.ZodType<PosDraft> = z.object({
  lines: z.array(cartLineSchema),
  customerId: z.string().nullable(),
  customerName: z.string().nullable(),
  globalDiscountMode: z.enum(['percentage', 'flat']),
  globalDiscountValue: z.string(),
  deliveryCharge: z.string(),
  otherFeeLabel: z.string(),
  otherFee: z.string(),
  advance: z.string(),
  advanceReference: z.string(),
  updatedAt: z.string(),
});

const persistedSchema: z.ZodType<PersistedPosDraftV1> = z.object({
  version: z.literal(1),
  active: draftSchema,
  held: z.array(z.object({ id: z.string().min(1), heldAt: z.string(), label: z.string().min(1), draft: draftSchema })),
});

export function emptyPosDraft(now = new Date().toISOString()): PosDraft {
  return {
    lines: [],
    customerId: null,
    customerName: null,
    globalDiscountMode: 'percentage',
    globalDiscountValue: '',
    deliveryCharge: '',
    otherFeeLabel: '',
    otherFee: '',
    advance: '',
    advanceReference: '',
    updatedAt: now,
  };
}

export function emptyPersistedDrafts(now = new Date().toISOString()): PersistedPosDraftV1 {
  return { version: 1, active: emptyPosDraft(now), held: [] };
}

export function parsePersistedDrafts(raw: string): PersistedPosDraftV1 {
  return persistedSchema.parse(JSON.parse(raw));
}

export function draftHasItems(draft: PosDraft): boolean {
  return draft.lines.length > 0;
}

export function heldCartLabel(draft: PosDraft, now = new Date()): string {
  if (draft.customerName?.trim()) return draft.customerName.trim();
  const first = draft.lines[0]?.name ?? 'Held cart';
  const remaining = draft.lines.length - 1;
  const contents = remaining > 0 ? `${first} +${remaining}` : first;
  return `${contents} · ${now.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}`;
}

export function holdActiveDraft(
  document: PersistedPosDraftV1,
  options: { id: string; now: string; label?: string },
): PersistedPosDraftV1 {
  if (!draftHasItems(document.active)) return document;
  const held: HeldCart = {
    id: options.id,
    heldAt: options.now,
    label: options.label ?? heldCartLabel(document.active, new Date(options.now)),
    draft: { ...document.active, updatedAt: options.now },
  };
  return { version: 1, active: emptyPosDraft(options.now), held: [held, ...document.held] };
}

export function resumeHeldDraft(
  document: PersistedPosDraftV1,
  heldId: string,
  options: { swapId: string; now: string },
): PersistedPosDraftV1 {
  const selected = document.held.find((entry) => entry.id === heldId);
  if (!selected) return document;
  const remaining = document.held.filter((entry) => entry.id !== heldId);
  const swapped = draftHasItems(document.active)
    ? [{ id: options.swapId, heldAt: options.now, label: heldCartLabel(document.active, new Date(options.now)), draft: { ...document.active, updatedAt: options.now } }, ...remaining]
    : remaining;
  return { version: 1, active: { ...selected.draft, updatedAt: options.now }, held: swapped };
}

export type DraftReconciliation = {
  draft: PosDraft;
  changedPrices: number;
  unavailable: number;
};

export function reconcileDraft(draft: PosDraft, products: readonly ShelfProduct[], now = new Date().toISOString()): DraftReconciliation {
  const shelf = new Map(products.map((product) => [product.id, product]));
  let changedPrices = 0;
  let unavailable = 0;
  const lines = draft.lines.map((line) => {
    const product = shelf.get(line.storeProductId);
    const missing = product === undefined || (product.availableQuantity !== undefined && Number(product.availableQuantity) <= 0);
    if (missing) {
      unavailable += 1;
      return { ...line, unavailable: true };
    }
    if (product.salePrice !== line.unitPrice) changedPrices += 1;
    return {
      ...line,
      sku: product.sku,
      name: product.name,
      unit: product.unit ?? line.unit,
      unitPrice: product.salePrice,
      // Rack travels with the shelf, not the cart: a rename or re-slotting
      // reaches held carts through the same reconcile that fixes prices.
      rack: product.rack ?? null,
      unavailable: false,
    };
  });
  return { draft: { ...draft, lines, updatedAt: now }, changedPrices, unavailable };
}

type DraftStatus = 'idle' | 'loading' | 'ready' | 'corrupt';

type PosDraftState = {
  scopeKey: string | null;
  status: DraftStatus;
  active: PosDraft;
  held: readonly HeldCart[];
  recoveryError: string | null;
  notice: string | null;
  hydrate: (organizationId: string, storeId: string) => Promise<void>;
  updateActive: (change: (draft: PosDraft) => PosDraft) => void;
  holdActive: () => boolean;
  resumeHeld: (heldId: string) => boolean;
  deleteHeld: (heldId: string) => void;
  clearActive: () => void;
  reconcile: (products: readonly ShelfProduct[]) => void;
  clearNotice: () => void;
  resetCorruptStorage: () => Promise<void>;
  flush: () => Promise<void>;
};

let writeChain: Promise<void> = Promise.resolve();

function storageKey(scopeKey: string): string {
  return `pos_drafts_v1:${scopeKey}`;
}

function newId(): string {
  return globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function snapshot(state: Pick<PosDraftState, 'active' | 'held'>): PersistedPosDraftV1 {
  return { version: 1, active: state.active, held: state.held };
}

function queueWrite(scopeKey: string, document: PersistedPosDraftV1): void {
  const raw = JSON.stringify(document);
  writeChain = writeChain.then(() => localRecord(storageKey(scopeKey)).write(raw));
}

export const usePosDrafts = create<PosDraftState>()((set, get) => ({
  scopeKey: null,
  status: 'idle',
  active: emptyPosDraft(),
  held: [],
  recoveryError: null,
  notice: null,
  hydrate: async (organizationId, storeId) => {
    const scopeKey = `${organizationId}:${storeId}`;
    if (get().scopeKey === scopeKey && get().status !== 'idle') return;
    await writeChain;
    set({ scopeKey, status: 'loading', recoveryError: null, notice: null, active: emptyPosDraft(), held: [] });
    const raw = await localRecord(storageKey(scopeKey)).read();
    if (get().scopeKey !== scopeKey) return;
    if (raw === null) {
      set({ status: 'ready' });
      return;
    }
    try {
      const document = parsePersistedDrafts(raw);
      set({ status: 'ready', active: document.active, held: document.held });
    } catch {
      set({ status: 'corrupt', recoveryError: 'Saved carts on this terminal could not be read. Reset them only after checking this terminal with a manager.' });
    }
  },
  updateActive: (change) => {
    const state = get();
    if (state.scopeKey === null || state.status !== 'ready') return;
    const active = { ...change(state.active), updatedAt: new Date().toISOString() };
    const next = { ...state, active };
    set({ active });
    queueWrite(state.scopeKey, snapshot(next));
  },
  holdActive: () => {
    const state = get();
    if (state.scopeKey === null || state.status !== 'ready' || !draftHasItems(state.active)) return false;
    const document = holdActiveDraft(snapshot(state), { id: newId(), now: new Date().toISOString() });
    set({ active: document.active, held: document.held, notice: 'Cart held. A new cart is ready.' });
    queueWrite(state.scopeKey, document);
    return true;
  },
  resumeHeld: (heldId) => {
    const state = get();
    if (state.scopeKey === null || state.status !== 'ready' || !state.held.some((entry) => entry.id === heldId)) return false;
    const document = resumeHeldDraft(snapshot(state), heldId, { swapId: newId(), now: new Date().toISOString() });
    set({ active: document.active, held: document.held, notice: draftHasItems(state.active) ? 'Carts swapped.' : 'Held cart resumed.' });
    queueWrite(state.scopeKey, document);
    return true;
  },
  deleteHeld: (heldId) => {
    const state = get();
    if (state.scopeKey === null || state.status !== 'ready') return;
    const held = state.held.filter((entry) => entry.id !== heldId);
    set({ held, notice: 'Held cart removed.' });
    queueWrite(state.scopeKey, { version: 1, active: state.active, held });
  },
  clearActive: () => {
    const state = get();
    if (state.scopeKey === null || state.status !== 'ready') return;
    const active = emptyPosDraft();
    set({ active, notice: null });
    queueWrite(state.scopeKey, { version: 1, active, held: state.held });
  },
  reconcile: (products) => {
    const state = get();
    if (state.scopeKey === null || state.status !== 'ready' || !draftHasItems(state.active)) return;
    const result = reconcileDraft(state.active, products);
    const unchanged = result.changedPrices === 0 && result.unavailable === state.active.lines.filter((line) => line.unavailable).length;
    if (unchanged) return;
    const notice = [
      result.changedPrices > 0 ? `${result.changedPrices} saved price${result.changedPrices === 1 ? '' : 's'} updated.` : '',
      result.unavailable > 0 ? `${result.unavailable} item${result.unavailable === 1 ? '' : 's'} unavailable; remove before checkout.` : '',
    ].filter(Boolean).join(' ');
    set({ active: result.draft, notice });
    queueWrite(state.scopeKey, { version: 1, active: result.draft, held: state.held });
  },
  clearNotice: () => set({ notice: null }),
  resetCorruptStorage: async () => {
    const state = get();
    if (state.scopeKey === null) return;
    const document = emptyPersistedDrafts();
    await localRecord(storageKey(state.scopeKey)).write(JSON.stringify(document));
    set({ status: 'ready', active: document.active, held: [], recoveryError: null, notice: 'Local carts reset.' });
  },
  flush: async () => { await writeChain; },
}));

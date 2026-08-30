'use client';

import type { ReceiveProductIdentity } from '@pharmacy/api';
import { create } from 'zustand';
import { z } from 'zod';

import { localRecord } from './database';

export type ReceiveCostMode = 'unit' | 'line';
export type ReceiveDraftLine = {
  id: string;
  identity: ReceiveProductIdentity;
  shelf?: { salePrice?: string; sku?: string; barcode?: string; rack?: string; minimumStock?: string };
  name: string;
  sku: string;
  unit: string;
  quantity: string;
  costMode: ReceiveCostMode;
  unitCost: string;
  lineTotal: string;
  batchNumber: string;
  expiryDate: string;
};
export type ReceiveDraft = {
  supplierId: string;
  supplierName: string;
  invoiceNumber: string;
  purchasedAt: string;
  supplierTotal: string;
  note: string;
  lines: readonly ReceiveDraftLine[];
  updatedAt: string;
};
export type HeldReceiveDraft = { id: string; heldAt: string; label: string; draft: ReceiveDraft };
export type PersistedReceiveDrafts = { version: 2; active: ReceiveDraft; held: readonly HeldReceiveDraft[] };

const lineSchema = z.object({
  id: z.string().min(1), identity: z.union([
    z.object({ storeProductId: z.string().min(1) }),
    z.object({ pharmacyProductId: z.string().min(1) }),
    z.object({ catalogProductId: z.string().min(1) }),
    z.object({ customProduct: z.object({ name: z.string().min(1), unit: z.string().min(1), barcode: z.string().optional() }) }),
  ]) as unknown as z.ZodType<ReceiveProductIdentity>, shelf: z.object({ salePrice: z.string().optional(), sku: z.string().optional(), barcode: z.string().optional(), rack: z.string().optional(), minimumStock: z.string().optional() }).optional(),
  name: z.string().min(1), sku: z.string(), unit: z.string().min(1), quantity: z.string(),
  costMode: z.enum(['unit', 'line']), unitCost: z.string(), lineTotal: z.string(),
  batchNumber: z.string(), expiryDate: z.string(),
}) as unknown as z.ZodType<ReceiveDraftLine>;
const draftSchema = z.object({
  supplierId: z.string(), supplierName: z.string(), invoiceNumber: z.string(), purchasedAt: z.string(), supplierTotal: z.string().default(''), note: z.string(),
  lines: z.array(lineSchema), updatedAt: z.string(),
});
const documentSchema = z.object({
  version: z.union([z.literal(1), z.literal(2)]), active: draftSchema,
  held: z.array(z.object({ id: z.string().min(1), heldAt: z.string(), label: z.string().min(1), draft: draftSchema })),
}).transform((document): PersistedReceiveDrafts => ({ ...document, version: 2 }));

export function emptyReceiveDraft(now = new Date().toISOString()): ReceiveDraft {
  return { supplierId: '', supplierName: '', invoiceNumber: '', purchasedAt: '', supplierTotal: '', note: '', lines: [], updatedAt: now };
}
export function emptyReceiveDocument(now = new Date().toISOString()): PersistedReceiveDrafts {
  return { version: 2, active: emptyReceiveDraft(now), held: [] };
}
export function parseReceiveDrafts(raw: string): PersistedReceiveDrafts { return documentSchema.parse(JSON.parse(raw)); }
export function receiveLineAmounts(line: Pick<ReceiveDraftLine, 'quantity' | 'costMode' | 'unitCost' | 'lineTotal'>): { unitCost: string; lineTotal: string; valid: boolean; hasCost: boolean } {
  const quantity = Number(line.quantity);
  const rawCost = line.costMode === 'unit' ? line.unitCost : line.lineTotal;
  if (!Number.isFinite(quantity) || quantity <= 0) return { unitCost: '', lineTotal: '', valid: false, hasCost: rawCost.trim() !== '' };
  if (rawCost.trim() === '') return { unitCost: '0.00', lineTotal: '0.00', valid: true, hasCost: false };
  const entered = Number(rawCost);
  if (!Number.isFinite(entered) || entered < 0) return { unitCost: '', lineTotal: '', valid: false, hasCost: true };
  return line.costMode === 'unit'
    ? { unitCost: entered.toFixed(2), lineTotal: (quantity * entered).toFixed(2), valid: true, hasCost: true }
    : { unitCost: (entered / quantity).toFixed(2), lineTotal: entered.toFixed(2), valid: true, hasCost: true };
}
export function receiveTotal(lines: readonly ReceiveDraftLine[]): string {
  return lines.reduce((sum, line) => { const amounts = receiveLineAmounts(line); return sum + (amounts.valid && amounts.hasCost ? Number(amounts.lineTotal) : 0); }, 0).toFixed(2);
}
export function receiveTotals(lines: readonly ReceiveDraftLine[], supplierTotal: string): { enteredTotal: string; total: string; unallocated: string; valid: boolean } {
  const enteredTotal = Number(receiveTotal(lines));
  if (supplierTotal.trim() === '') return { enteredTotal: enteredTotal.toFixed(2), total: enteredTotal.toFixed(2), unallocated: '0.00', valid: true };
  const explicitTotal = Number(supplierTotal);
  const valid = Number.isFinite(explicitTotal) && explicitTotal >= 0 && explicitTotal + 0.000001 >= enteredTotal;
  return {
    enteredTotal: enteredTotal.toFixed(2),
    total: Number.isFinite(explicitTotal) && explicitTotal >= 0 ? explicitTotal.toFixed(2) : '0.00',
    unallocated: Number.isFinite(explicitTotal) ? (explicitTotal - enteredTotal).toFixed(2) : '0.00',
    valid,
  };
}

function key(scope: string): string { return `receive_drafts_v1:${scope}`; }
function newId(): string { return globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random().toString(16).slice(2)}`; }
function snapshot(state: Pick<ReceiveDraftState, 'active' | 'held'>): PersistedReceiveDrafts { return { version: 2, active: state.active, held: state.held }; }
function label(draft: ReceiveDraft): string { return draft.supplierName.trim() || draft.lines[0]?.name || 'Held receipt'; }
let chain: Promise<void> = Promise.resolve();
function persist(scope: string, document: PersistedReceiveDrafts): void { chain = chain.then(() => localRecord(key(scope)).write(JSON.stringify(document))); }

type DraftStatus = 'idle' | 'loading' | 'ready' | 'corrupt';
type ReceiveDraftState = {
  scopeKey: string | null; status: DraftStatus; active: ReceiveDraft; held: readonly HeldReceiveDraft[]; recoveryError: string | null; notice: string | null;
  hydrate: (organizationId: string, storeId: string) => Promise<void>;
  updateActive: (change: (draft: ReceiveDraft) => ReceiveDraft) => void;
  holdActive: () => boolean; resumeHeld: (id: string) => boolean; deleteHeld: (id: string) => void; clearActive: () => void;
  resetCorruptStorage: () => Promise<void>; flush: () => Promise<void>;
};

export const useReceiveDrafts = create<ReceiveDraftState>()((set, get) => ({
  scopeKey: null, status: 'idle', active: emptyReceiveDraft(), held: [], recoveryError: null, notice: null,
  hydrate: async (organizationId, storeId) => {
    const scope = `${organizationId}:${storeId}`;
    if (get().scopeKey === scope && get().status !== 'idle') return;
    await chain; set({ scopeKey: scope, status: 'loading', active: emptyReceiveDraft(), held: [], recoveryError: null, notice: null });
    const raw = await localRecord(key(scope)).read();
    if (get().scopeKey !== scope) return;
    if (raw === null) { set({ status: 'ready' }); return; }
    try { const document = parseReceiveDrafts(raw); set({ status: 'ready', active: document.active, held: document.held }); }
    catch { set({ status: 'corrupt', recoveryError: 'Saved receiving carts on this terminal could not be read.' }); }
  },
  updateActive: (change) => {
    const state = get(); if (!state.scopeKey || state.status !== 'ready') return;
    const active = { ...change(state.active), updatedAt: new Date().toISOString() }; set({ active }); persist(state.scopeKey, { version: 2, active, held: state.held });
  },
  holdActive: () => {
    const state = get(); if (!state.scopeKey || state.status !== 'ready' || state.active.lines.length === 0) return false;
    const now = new Date().toISOString(); const held = [{ id: newId(), heldAt: now, label: label(state.active), draft: { ...state.active, updatedAt: now } }, ...state.held];
    const active = emptyReceiveDraft(now); set({ active, held, notice: 'Receipt held. Payment fields were cleared.' }); persist(state.scopeKey, { version: 2, active, held }); return true;
  },
  resumeHeld: (id) => {
    const state = get(); if (!state.scopeKey || state.status !== 'ready') return false;
    const chosen = state.held.find((entry) => entry.id === id); if (!chosen) return false;
    const now = new Date().toISOString(); const rest = state.held.filter((entry) => entry.id !== id);
    const held = state.active.lines.length > 0 ? [{ id: newId(), heldAt: now, label: label(state.active), draft: state.active }, ...rest] : rest;
    const active = { ...chosen.draft, updatedAt: now }; set({ active, held, notice: 'Held receipt resumed. Re-enter its payment.' }); persist(state.scopeKey, { version: 2, active, held }); return true;
  },
  deleteHeld: (id) => { const state = get(); if (!state.scopeKey) return; const held = state.held.filter((entry) => entry.id !== id); set({ held }); persist(state.scopeKey, { version: 2, active: state.active, held }); },
  clearActive: () => { const state = get(); if (!state.scopeKey) return; const active = emptyReceiveDraft(); set({ active, notice: null }); persist(state.scopeKey, { version: 2, active, held: state.held }); },
  resetCorruptStorage: async () => { const state = get(); if (!state.scopeKey) return; const document = emptyReceiveDocument(); await localRecord(key(state.scopeKey)).write(JSON.stringify(document)); set({ status: 'ready', active: document.active, held: [], recoveryError: null }); },
  flush: async () => { await chain; },
}));

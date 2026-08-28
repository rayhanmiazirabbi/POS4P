import { describe, expect, it } from 'vitest';

import {
  emptyPersistedDrafts,
  emptyPosDraft,
  holdActiveDraft,
  parsePersistedDrafts,
  reconcileDraft,
  resumeHeldDraft,
  type CartLine,
} from './pos-drafts';

const line: CartLine = {
  storeProductId: 'shelf-1', sku: 'NAPA-500', name: 'Napa', unit: 'tablet', quantity: 2,
  unitPrice: '5.00', discountMode: 'percentage', discountValue: '',
};

describe('persisted POS drafts', () => {
  it('round-trips the versioned document without tender or receipt state', () => {
    const document = { ...emptyPersistedDrafts('2026-08-28T10:00:00Z'), active: { ...emptyPosDraft('2026-08-28T10:00:00Z'), lines: [line], customerName: 'Rahim' } };
    expect(parsePersistedDrafts(JSON.stringify(document))).toEqual(document);
    expect(JSON.stringify(document)).not.toContain('cashReceived');
    expect(JSON.stringify(document)).not.toContain('receipt');
  });

  it('holds an active cart and starts a clean draft', () => {
    const document = { ...emptyPersistedDrafts(), active: { ...emptyPosDraft(), lines: [line] } };
    const held = holdActiveDraft(document, { id: 'held-1', now: '2026-08-28T10:30:00Z', label: 'Counter order' });
    expect(held.active.lines).toEqual([]);
    expect(held.held[0]).toMatchObject({ id: 'held-1', label: 'Counter order', draft: { lines: [line] } });
  });

  it('swaps a non-empty active cart when resuming', () => {
    const first = { ...emptyPosDraft(), lines: [line] };
    const secondLine = { ...line, storeProductId: 'shelf-2', name: 'Ace' };
    const second = { ...emptyPosDraft(), lines: [secondLine] };
    const resumed = resumeHeldDraft({ version: 1, active: first, held: [{ id: 'held-2', label: 'Ace', heldAt: 'x', draft: second }] }, 'held-2', { swapId: 'held-1', now: '2026-08-28T11:00:00Z' });
    expect(resumed.active.lines[0]?.name).toBe('Ace');
    expect(resumed.held[0]?.draft.lines[0]?.name).toBe('Napa');
  });

  it('refreshes saved prices and marks missing stock unavailable', () => {
    const draft = { ...emptyPosDraft(), lines: [line, { ...line, storeProductId: 'missing' }] };
    const result = reconcileDraft(draft, [{ id: 'shelf-1', sku: 'NAPA', name: 'Napa 500', unit: 'tablet', salePrice: '6.00', barcode: null, rack: null, availableQuantity: '8' }]);
    expect(result.changedPrices).toBe(1);
    expect(result.unavailable).toBe(1);
    expect(result.draft.lines[0]).toMatchObject({ name: 'Napa 500', unitPrice: '6.00', unavailable: false });
    expect(result.draft.lines[1]?.unavailable).toBe(true);
  });

  it('refuses a malformed persisted payload', () => {
    expect(() => parsePersistedDrafts('{"version":1,"active":{}}')).toThrow();
  });
});

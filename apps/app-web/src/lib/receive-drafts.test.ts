import { describe, expect, it } from 'vitest';

import { emptyReceiveDocument, parseReceiveDrafts, receiveLineAmounts, receiveTotal, receiveTotals, type ReceiveDraftLine } from './receive-drafts';

const line: ReceiveDraftLine = {
  id: 'line-1', identity: { storeProductId: 'shelf-1' }, name: 'Napa', sku: 'NAPA', unit: 'box',
  quantity: '3', costMode: 'line', unitCost: '', lineTotal: '100', batchNumber: '', expiryDate: '',
};

describe('receiving drafts and costs', () => {
  it('preserves an authoritative line total while deriving unit cost', () => {
    expect(receiveLineAmounts(line)).toEqual({ unitCost: '33.33', lineTotal: '100.00', valid: true, hasCost: true });
    expect(receiveLineAmounts({ ...line, costMode: 'unit', unitCost: '4.25' })).toEqual({ unitCost: '4.25', lineTotal: '12.75', valid: true, hasCost: true });
  });

  it('accepts a blank cost as a valid zero-cost receiving line', () => {
    expect(receiveLineAmounts({ ...line, costMode: 'unit', unitCost: '', lineTotal: '' })).toEqual({ unitCost: '0.00', lineTotal: '0.00', valid: true, hasCost: false });
  });

  it('uses an optional supplier total and validates it against entered item costs', () => {
    expect(receiveTotals([line], '')).toEqual({ enteredTotal: '100.00', total: '100.00', unallocated: '0.00', valid: true });
    expect(receiveTotals([line], '125')).toEqual({ enteredTotal: '100.00', total: '125.00', unallocated: '25.00', valid: true });
    expect(receiveTotals([line], '99')).toEqual({ enteredTotal: '100.00', total: '99.00', unallocated: '-1.00', valid: false });
  });

  it('totals valid lines, upgrades v1 drafts, and rejects malformed persisted data', () => {
    expect(receiveTotal([line, { ...line, id: 'line-2', costMode: 'unit', unitCost: '10', quantity: '2' }])).toBe('120.00');
    const document = { ...emptyReceiveDocument(), active: { ...emptyReceiveDocument().active, lines: [line] } };
    expect(parseReceiveDrafts(JSON.stringify(document))).toEqual(document);
    const { supplierTotal: _supplierTotal, ...legacyDraft } = document.active;
    const legacy = { version: 1, active: legacyDraft, held: [] };
    expect(parseReceiveDrafts(JSON.stringify(legacy))).toEqual({ version: 2, active: { ...legacyDraft, supplierTotal: '' }, held: [] });
    expect(() => parseReceiveDrafts('{"version":1}')).toThrow();
  });
});

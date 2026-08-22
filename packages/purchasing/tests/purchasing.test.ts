import { describe, expect, it } from 'vitest';
import { applyConfirmation, calculatePurchaseTotals, confirmPurchase, createPurchaseDraft, createPurchaseReturn, draftState, purchaseReturnAmount, receiptBatches, supplierDue, validateReceipt, type PurchaseDraft } from '../src/index';
import { money } from '@pharmacy/money';

const item = { id: 'i1', storeProductId: 'sp1', supplierDescription: 'Entered supplier text', quantity: 3, unitCost: money('10.00'), expiryDate: '2027-01-01' };
const draft: PurchaseDraft = { id: 'p1', supplierId: 's1', invoiceNumber: 'INV-1', createdAt: '2026-08-21T00:00:00Z', items: [item] };

describe('purchasing', () => {
  it('keeps drafts stock-neutral and calculates due', () => { expect(calculatePurchaseTotals(createPurchaseDraft(draft).items, money('5.00'))).toMatchObject({ subtotal: money('30.00'), due: money('25.00') }); });
  it('requires an idempotent confirmation for the matching draft', () => { expect(confirmPurchase(draft, { purchaseId: 'p1', idempotencyKey: 'purchase-confirm-1', confirmedAt: '2026-08-21T00:00:00Z' }).purchaseId).toBe('p1'); expect(() => confirmPurchase(draft, { purchaseId: 'other', idempotencyKey: 'purchase-confirm-1', confirmedAt: draft.createdAt })).toThrow('match'); });
});

describe('confirmation state', () => {
  const confirmation = { purchaseId: 'p1', idempotencyKey: 'purchase-confirm-1', confirmedAt: '2026-08-21T01:00:00Z' };
  it('replays the same key idempotently and rejects conflicting keys', () => {
    const state = draftState(draft);
    const first = applyConfirmation(state, confirmation);
    expect(first.status).toBe('confirmed');
    expect(applyConfirmation(first, confirmation)).toEqual(first);
    expect(() => applyConfirmation(first, { ...confirmation, idempotencyKey: 'purchase-confirm-2' })).toThrow('different idempotency key');
    expect(() => applyConfirmation(state, { ...confirmation, idempotencyKey: 'short' })).toThrow('too short');
  });
});

describe('receipts', () => {
  it('accepts multiple batches that sum to the ordered quantity', () => {
    const receipt = { purchaseId: 'p1', receivedAt: '2026-08-21', items: [{ itemId: 'i1', batches: [{ batchNumber: 'B1', quantity: 2, expiryDate: '2027-01-01' }, { batchNumber: 'B2', quantity: 1, expiryDate: '2026-12-01' }] }] };
    expect(validateReceipt(receipt, draft)).toBeUndefined();
    expect(receiptBatches(receipt, draft)).toEqual([
      { storeProductId: 'sp1', lotNumber: 'B1', quantity: 2, expiryDate: '2027-01-01', supplierDescription: 'Entered supplier text' },
      { storeProductId: 'sp1', lotNumber: 'B2', quantity: 1, expiryDate: '2026-12-01', supplierDescription: 'Entered supplier text' },
    ]);
  });
  it('rejects short receipts, bad expiries, and duplicate batches', () => {
    expect(() => validateReceipt({ purchaseId: 'p1', receivedAt: '2026-08-21', items: [{ itemId: 'i1', batches: [{ batchNumber: 'B1', quantity: 2, expiryDate: '2027-01-01' }] }] }, draft)).toThrow('Received 2 of 3');
    expect(() => validateReceipt({ purchaseId: 'p1', receivedAt: '2026-08-21', items: [{ itemId: 'i1', batches: [{ batchNumber: 'B1', quantity: 3, expiryDate: '2026-08-21' }] }] }, draft)).toThrow('expires on or before');
    expect(() => validateReceipt({ purchaseId: 'p1', receivedAt: '2026-08-21', items: [{ itemId: 'i1', batches: [{ batchNumber: 'B1', quantity: 2, expiryDate: '2027-01-01' }, { batchNumber: 'B1', quantity: 1, expiryDate: '2027-01-01' }] }] }, draft)).toThrow('Duplicate batch number');
    expect(() => validateReceipt({ purchaseId: 'p9', receivedAt: '2026-08-21', items: [] }, draft)).toThrow('does not match');
  });
});

describe('purchase returns', () => {
  it('limits returns to remaining purchased quantities', () => {
    const returned = createPurchaseReturn({ id: 'r1', purchaseId: 'p1', lines: [{ itemId: 'i1', quantity: 2 }], createdAt: '2026-08-22T00:00:00Z' }, draft);
    expect(purchaseReturnAmount(returned, draft)).toEqual(money('20.00'));
    expect(() => createPurchaseReturn({ id: 'r2', purchaseId: 'p1', lines: [{ itemId: 'i1', quantity: 2 }], createdAt: '2026-08-22T00:00:00Z' }, draft, { i1: 2 })).toThrow('exceeds');
    expect(() => createPurchaseReturn({ id: 'r3', purchaseId: 'p1', lines: [], createdAt: '2026-08-22T00:00:00Z' }, draft)).toThrow('must contain lines');
  });
});

describe('supplier ledger', () => {
  it('reconciles due across purchases, payments, returns, and adjustments', () => {
    const due = supplierDue([
      { id: 'e1', kind: 'purchase', amount: money('30.00'), purchaseId: 'p1', createdAt: '2026-08-21T00:00:00Z' },
      { id: 'e2', kind: 'payment', amount: money('10.00'), createdAt: '2026-08-21T01:00:00Z' },
      { id: 'e3', kind: 'return', amount: money('5.00'), purchaseId: 'p1', createdAt: '2026-08-22T00:00:00Z' },
      { id: 'e4', kind: 'adjustment', amount: money('-2.50'), createdAt: '2026-08-22T01:00:00Z' },
    ]);
    expect(due).toEqual(money('12.50'));
    expect(supplierDue([])).toEqual(money('0.00'));
    expect(supplierDue([
      { id: 'e1', kind: 'purchase', amount: money('30.00'), purchaseId: 'p1', createdAt: '2026-08-21T00:00:00Z' },
      { id: 'e2', kind: 'payment', amount: money('40.00'), createdAt: '2026-08-21T01:00:00Z' },
    ])).toEqual(money('-10.00'));
  });
});

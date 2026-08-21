import { describe, expect, it } from 'vitest';
import { calculatePurchaseTotals, confirmPurchase, createPurchaseDraft, type PurchaseDraft } from '../src/index';
import { money } from '@pharmacy/money';

const draft: PurchaseDraft = { id: 'p1', supplierId: 's1', invoiceNumber: 'INV-1', createdAt: '2026-08-21T00:00:00Z', items: [{ id: 'i1', storeProductId: 'sp1', supplierDescription: 'Entered supplier text', quantity: 3, unitCost: money('10.00'), expiryDate: '2027-01-01' }] };
describe('purchasing', () => {
  it('keeps drafts stock-neutral and calculates due', () => { expect(calculatePurchaseTotals(createPurchaseDraft(draft).items, money('5.00'))).toMatchObject({ subtotal: money('30.00'), due: money('25.00') }); });
  it('requires an idempotent confirmation for the matching draft', () => { expect(confirmPurchase(draft, { purchaseId: 'p1', idempotencyKey: 'purchase-confirm-1', confirmedAt: '2026-08-21T00:00:00Z' }).purchaseId).toBe('p1'); expect(() => confirmPurchase(draft, { purchaseId: 'other', idempotencyKey: 'purchase-confirm-1', confirmedAt: draft.createdAt })).toThrow('match'); });
});

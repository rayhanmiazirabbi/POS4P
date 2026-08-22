import { add, money, multiply, subtract, type MoneyValue } from '@pharmacy/money';

export type PurchaseItem = { id: string; storeProductId: string; supplierDescription: string; quantity: number; unitCost: MoneyValue; expiryDate: string; batchNumber?: string };
export type PurchaseDraft = { id: string; supplierId: string; invoiceNumber: string; items: readonly PurchaseItem[]; createdAt: string };
export type PurchaseConfirmation = { purchaseId: string; idempotencyKey: string; confirmedAt: string };
export type PurchaseTotals = { subtotal: MoneyValue; due: MoneyValue };
export type PurchaseBatch = { batchNumber: string; quantity: number; expiryDate: string };
export type PurchaseItemReceipt = { itemId: string; batches: readonly PurchaseBatch[] };
export type PurchaseReceipt = { purchaseId: string; receivedAt: string; items: readonly PurchaseItemReceipt[] };
export type PurchaseReturnLine = { itemId: string; quantity: number };
export type PurchaseReturn = { id: string; purchaseId: string; lines: readonly PurchaseReturnLine[]; createdAt: string };
export type SupplierLedgerKind = 'purchase' | 'return' | 'payment' | 'adjustment';
export type SupplierLedgerEntry = { id: string; kind: SupplierLedgerKind; amount: MoneyValue; purchaseId?: string; createdAt: string };
export type ConfirmationState = { purchaseId: string; idempotencyKey: string | null; status: 'draft' | 'confirmed'; confirmedAt: string | null };

const DATE_PATTERN = /^\d{4}-\d{2}-\d{2}$/;

function assertWhole(quantity: number, message: string): void { if (!Number.isInteger(quantity) || quantity <= 0) throw new Error(message); }
function assertDate(value: string, message: string): void { if (!DATE_PATTERN.test(value) || Number.isNaN(Date.parse(`${value}T00:00:00.000Z`))) throw new Error(message); }

export function calculatePurchaseTotals(items: readonly PurchaseItem[], paid: MoneyValue = money('0.00')): PurchaseTotals { const subtotal = add(...items.map((item) => multiply(item.unitCost, item.quantity))); return { subtotal, due: subtract(subtotal, paid) }; }
export function createPurchaseDraft(input: PurchaseDraft): PurchaseDraft { if (input.items.length === 0) throw new Error('Purchase must contain items'); return { ...input, items: input.items.map((item) => ({ ...item, unitCost: { ...item.unitCost } })) }; }
export function confirmPurchase(draft: PurchaseDraft, confirmation: PurchaseConfirmation): PurchaseConfirmation { if (confirmation.purchaseId !== draft.id) throw new Error('Purchase confirmation does not match draft'); if (confirmation.idempotencyKey.trim().length < 16) throw new Error('Idempotency key is too short'); return { ...confirmation }; }

export function draftState(draft: PurchaseDraft): ConfirmationState { return { purchaseId: draft.id, idempotencyKey: null, status: 'draft', confirmedAt: null }; }

export function applyConfirmation(state: ConfirmationState, confirmation: PurchaseConfirmation): ConfirmationState {
  if (confirmation.purchaseId !== state.purchaseId) throw new Error('Purchase confirmation does not match draft');
  if (confirmation.idempotencyKey.trim().length < 16) throw new Error('Idempotency key is too short');
  if (state.status === 'confirmed') {
    if (state.idempotencyKey !== confirmation.idempotencyKey) throw new Error('Purchase already confirmed with a different idempotency key');
    return state;
  }
  return { purchaseId: state.purchaseId, idempotencyKey: confirmation.idempotencyKey, status: 'confirmed', confirmedAt: confirmation.confirmedAt };
}

export function validateReceipt(receipt: PurchaseReceipt, draft: PurchaseDraft): void {
  if (receipt.purchaseId !== draft.id) throw new Error('Receipt does not match purchase draft');
  assertDate(receipt.receivedAt, 'Receipt date must be a valid date');
  const items = new Map(draft.items.map((item) => [item.id, item]));
  const seenItems = new Set<string>();
  for (const itemReceipt of receipt.items) {
    const item = items.get(itemReceipt.itemId);
    if (!item) throw new Error(`Unknown purchase item: ${itemReceipt.itemId}`);
    if (seenItems.has(itemReceipt.itemId)) throw new Error(`Duplicate receipt for item ${itemReceipt.itemId}`);
    seenItems.add(itemReceipt.itemId);
    if (itemReceipt.batches.length === 0) throw new Error(`Item ${itemReceipt.itemId} must contain at least one batch`);
    const batchNumbers = new Set<string>();
    let received = 0;
    for (const batch of itemReceipt.batches) {
      assertWhole(batch.quantity, 'Batch quantity must be a positive integer');
      assertDate(batch.expiryDate, 'Batch expiry must be a valid date');
      if (batch.expiryDate <= receipt.receivedAt.slice(0, 10)) throw new Error(`Batch ${batch.batchNumber} expires on or before receipt date`);
      if (batch.batchNumber.trim().length === 0) throw new Error('Batch number is required');
      if (batchNumbers.has(batch.batchNumber)) throw new Error(`Duplicate batch number ${batch.batchNumber}`);
      batchNumbers.add(batch.batchNumber);
      received += batch.quantity;
    }
    if (received !== item.quantity) throw new Error(`Received ${received} of ${item.quantity} for item ${itemReceipt.itemId}`);
  }
  if (seenItems.size !== draft.items.length) throw new Error('Receipt must cover every purchase item');
}

export function receiptBatches(receipt: PurchaseReceipt, draft: PurchaseDraft): Array<{ storeProductId: string; lotNumber: string; quantity: number; expiryDate: string; supplierDescription: string }> {
  validateReceipt(receipt, draft);
  const items = new Map(draft.items.map((item) => [item.id, item]));
  return receipt.items.flatMap((itemReceipt) => {
    const item = items.get(itemReceipt.itemId);
    if (!item) return [];
    return itemReceipt.batches.map((batch) => ({ storeProductId: item.storeProductId, lotNumber: batch.batchNumber, quantity: batch.quantity, expiryDate: batch.expiryDate, supplierDescription: item.supplierDescription }));
  });
}

export function createPurchaseReturn(input: Omit<PurchaseReturn, 'lines'> & { lines: readonly PurchaseReturnLine[] }, draft: PurchaseDraft, alreadyReturned: Readonly<Record<string, number>> = {}): PurchaseReturn {
  if (input.purchaseId !== draft.id) throw new Error('Return does not match purchase draft');
  if (input.lines.length === 0) throw new Error('Return must contain lines');
  const items = new Map(draft.items.map((item) => [item.id, item]));
  for (const line of input.lines) {
    const item = items.get(line.itemId);
    if (!item) throw new Error(`Unknown purchase item: ${line.itemId}`);
    assertWhole(line.quantity, 'Return quantity must be a positive integer');
    if (line.quantity + (alreadyReturned[line.itemId] ?? 0) > item.quantity) throw new Error(`Return quantity exceeds purchased quantity for item ${line.itemId}`);
  }
  return { ...input, lines: input.lines.map((line) => ({ ...line })) };
}

export function purchaseReturnAmount(returned: PurchaseReturn, draft: PurchaseDraft): MoneyValue {
  const items = new Map(draft.items.map((item) => [item.id, item]));
  return add(...returned.lines.map((line) => {
    const item = items.get(line.itemId);
    if (!item) throw new Error(`Unknown purchase item: ${line.itemId}`);
    return multiply(item.unitCost, line.quantity);
  }));
}

export function supplierDue(entries: readonly SupplierLedgerEntry[]): MoneyValue {
  const zero = money('0.00');
  const signed = entries.map((entry) => {
    if (entry.kind === 'purchase') return entry.amount;
    if (entry.kind === 'adjustment') return entry.amount;
    return subtract(zero, entry.amount);
  });
  return add(...signed);
}

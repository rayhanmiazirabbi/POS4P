import { add, money, multiply, type MoneyValue } from '@pharmacy/money';

export type PurchaseItem = { id: string; storeProductId: string; supplierDescription: string; quantity: number; unitCost: MoneyValue; expiryDate: string; batchNumber?: string };
export type PurchaseDraft = { id: string; supplierId: string; invoiceNumber: string; items: readonly PurchaseItem[]; createdAt: string };
export type PurchaseConfirmation = { purchaseId: string; idempotencyKey: string; confirmedAt: string };
export type PurchaseTotals = { subtotal: MoneyValue; due: MoneyValue };

export function calculatePurchaseTotals(items: readonly PurchaseItem[], paid: MoneyValue = money('0.00')): PurchaseTotals { const subtotal = add(...items.map((item) => multiply(item.unitCost, item.quantity))); return { subtotal, due: add(subtotal, money(`-${paid.amount}`)) }; }
export function createPurchaseDraft(input: PurchaseDraft): PurchaseDraft { if (input.items.length === 0) throw new Error('Purchase must contain items'); return { ...input, items: input.items.map((item) => ({ ...item, unitCost: { ...item.unitCost } })) }; }
export function confirmPurchase(draft: PurchaseDraft, confirmation: PurchaseConfirmation): PurchaseConfirmation { if (confirmation.purchaseId !== draft.id) throw new Error('Purchase confirmation does not match draft'); if (confirmation.idempotencyKey.trim().length < 16) throw new Error('Idempotency key is too short'); return { ...confirmation }; }

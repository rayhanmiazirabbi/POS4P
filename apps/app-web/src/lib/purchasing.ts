import type { PurchaseOrder, PurchaseOrderStatusWire } from '@pharmacy/api';

export type PurchasingView = 'replenishment' | 'orders' | 'history';

export const purchasingViews: readonly PurchasingView[] = ['replenishment', 'orders', 'history'];

export function purchasingView(value: string | null): PurchasingView {
  return purchasingViews.includes(value as PurchasingView) ? value as PurchasingView : 'replenishment';
}

export function statusLabel(status: PurchaseOrderStatusWire): string {
  return status.replaceAll('_', ' ').replace(/^./, (letter) => letter.toUpperCase());
}

export function orderProgress(order: Pick<PurchaseOrder, 'orderedQuantity' | 'receivedQuantity'>): number {
  const ordered = Number(order.orderedQuantity);
  if (!Number.isFinite(ordered) || ordered <= 0) return 0;
  return Math.min(Math.max(Number(order.receivedQuantity) / ordered, 0), 1);
}

export function quantityText(value: string): string {
  const quantity = Number(value);
  if (!Number.isFinite(quantity)) return value;
  return new Intl.NumberFormat('en-BD', { maximumFractionDigits: 4 }).format(quantity);
}

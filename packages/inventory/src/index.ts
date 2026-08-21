export type StockBatch = { id: string; productId: string; lotNumber: string; expiryDate: string; onHand: number; reserved: number; receivedAt: string };
export type StockAllocation = { batchId: string; quantity: number };
export type AllocationResult = { status: 'allocated' | 'partial' | 'failed'; requested: number; allocated: number; remaining: number; allocations: readonly StockAllocation[] };
export type MovementKind = 'purchase' | 'sale' | 'return' | 'adjustment' | 'transfer-in' | 'transfer-out';
export type StockMovement = { id: string; batchId: string; productId: string; quantity: number; kind: MovementKind; referenceId: string; createdAt: string };

export function availableStock(batch: Pick<StockBatch, 'onHand' | 'reserved'>): number { return Math.max(0, batch.onHand - batch.reserved); }
export function totalAvailableStock(batches: readonly StockBatch[]): number { return batches.reduce((sum, batch) => sum + availableStock(batch), 0); }

export function selectFefoBatches(batches: readonly StockBatch[], required: number, asOfDate: string): StockBatch[] {
  if (!Number.isInteger(required) || required < 0) throw new Error('Required quantity must be a non-negative integer');
  return batches.filter((batch) => batch.expiryDate >= asOfDate && availableStock(batch) > 0)
    .sort((a, b) => a.expiryDate.localeCompare(b.expiryDate) || a.receivedAt.localeCompare(b.receivedAt) || a.id.localeCompare(b.id));
}

export function allocateFefo(batches: readonly StockBatch[], required: number, asOfDate: string): AllocationResult {
  if (!Number.isInteger(required) || required < 0) throw new Error('Required quantity must be a non-negative integer');
  let remaining = required;
  const allocations: StockAllocation[] = [];
  for (const batch of selectFefoBatches(batches, required, asOfDate)) {
    if (remaining === 0) break;
    const quantity = Math.min(remaining, availableStock(batch));
    allocations.push({ batchId: batch.id, quantity });
    remaining -= quantity;
  }
  const allocated = required - remaining;
  return { status: remaining === 0 ? 'allocated' : allocated === 0 ? 'failed' : 'partial', requested: required, allocated, remaining, allocations };
}

export function createStockMovement(input: Omit<StockMovement, 'quantity'> & { quantity: number }): StockMovement {
  if (!Number.isInteger(input.quantity) || input.quantity === 0) throw new Error('Movement quantity must be a non-zero integer');
  return { ...input };
}

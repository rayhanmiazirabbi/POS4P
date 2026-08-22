export type StockBatch = { id: string; productId: string; lotNumber: string; expiryDate: string; onHand: number; reserved: number; receivedAt: string };
export type StockAllocation = { batchId: string; quantity: number };
export type AllocationResult = { status: 'allocated' | 'partial' | 'failed'; requested: number; allocated: number; remaining: number; allocations: readonly StockAllocation[] };
export type MovementKind = 'purchase' | 'sale' | 'return' | 'adjustment' | 'transfer-in' | 'transfer-out';
export type StockMovement = { id: string; batchId: string; productId: string; quantity: number; kind: MovementKind; referenceId: string; createdAt: string };
export type Reservation = { status: 'reserved' | 'failed'; batches: readonly StockBatch[]; allocations: readonly StockAllocation[] };
export type ProductBalance = { productId: string; onHand: number; reserved: number };
export type StockLevel = { productId: string; available: number };

const INCREASING: ReadonlySet<MovementKind> = new Set(['purchase', 'return', 'transfer-in']);

function assertWhole(quantity: number, message: string): void { if (!Number.isInteger(quantity) || quantity < 0) throw new Error(message); }
function isExpired(batch: StockBatch, asOfDate: string): boolean { return batch.expiryDate < asOfDate; }
function replaceBatch(batches: readonly StockBatch[], updated: StockBatch): StockBatch[] { return batches.map((batch) => (batch.id === updated.id ? updated : batch)); }

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

export function applyStockMovement(batch: StockBatch, movement: Pick<StockMovement, 'kind' | 'quantity'>, asOfDate: string): StockBatch {
  const { kind, quantity } = movement;
  if (kind === 'adjustment') {
    if (!Number.isInteger(quantity) || quantity === 0) throw new Error('Adjustment quantity must be a non-zero integer');
    const onHand = batch.onHand + quantity;
    if (onHand < 0) throw new Error('Adjustment would make stock negative');
    return { ...batch, onHand };
  }
  assertWhole(quantity, 'Movement quantity must be a non-negative integer');
  if (quantity === 0) throw new Error('Movement quantity must be greater than zero');
  if ((kind === 'sale' || kind === 'transfer-out') && isExpired(batch, asOfDate)) throw new Error(`Batch ${batch.id} is expired as of ${asOfDate}`);
  if (INCREASING.has(kind)) return { ...batch, onHand: batch.onHand + quantity };
  if (quantity > availableStock(batch)) throw new Error(`Insufficient available stock in batch ${batch.id}`);
  return { ...batch, onHand: batch.onHand - quantity };
}

export function reserveStock(batches: readonly StockBatch[], required: number, asOfDate: string): Reservation {
  const allocation = allocateFefo(batches, required, asOfDate);
  if (allocation.status !== 'allocated') return { status: 'failed', batches, allocations: [] };
  const reserved = new Map(allocation.allocations.map((item) => [item.batchId, item.quantity]));
  const updated = batches.map((batch) => (reserved.has(batch.id) ? { ...batch, reserved: batch.reserved + (reserved.get(batch.id) ?? 0) } : batch));
  return { status: 'reserved', batches: updated, allocations: allocation.allocations };
}

export function releaseReservation(batches: readonly StockBatch[], allocations: readonly StockAllocation[]): StockBatch[] {
  const releasing = new Map<string, number>();
  for (const item of allocations) {
    assertWhole(item.quantity, 'Release quantity must be a non-negative integer');
    releasing.set(item.batchId, (releasing.get(item.batchId) ?? 0) + item.quantity);
  }
  return batches.map((batch) => {
    const quantity = releasing.get(batch.id);
    if (quantity === undefined) return batch;
    if (quantity > batch.reserved) throw new Error(`Cannot release more than reserved for batch ${batch.id}`);
    return { ...batch, reserved: batch.reserved - quantity };
  });
}

export function rebuildBalances(movements: readonly StockMovement[], batches: readonly StockBatch[] = []): ProductBalance[] {
  const reserved = new Map<string, number>();
  for (const batch of batches) reserved.set(batch.productId, (reserved.get(batch.productId) ?? 0) + batch.reserved);
  const onHand = new Map<string, number>();
  const ordered = [...movements].sort((a, b) => a.createdAt.localeCompare(b.createdAt) || a.id.localeCompare(b.id));
  for (const movement of ordered) {
    const current = onHand.get(movement.productId) ?? 0;
    const updated = current + movement.quantity;
    if (updated < 0) throw new Error(`Ledger would make stock negative for product ${movement.productId}`);
    onHand.set(movement.productId, updated);
  }
  const productIds = new Set([...onHand.keys(), ...reserved.keys()]);
  return [...productIds].sort().map((productId) => ({ productId, onHand: onHand.get(productId) ?? 0, reserved: reserved.get(productId) ?? 0 }));
}

export function expiringBatches(batches: readonly StockBatch[], asOfDate: string, withinDays: number): StockBatch[] {
  assertWhole(withinDays, 'withinDays must be a non-negative integer');
  const until = new Date(`${asOfDate}T00:00:00.000Z`);
  until.setUTCDate(until.getUTCDate() + withinDays);
  const untilDate = until.toISOString().slice(0, 10);
  return batches.filter((batch) => batch.expiryDate >= asOfDate && batch.expiryDate <= untilDate)
    .sort((a, b) => a.expiryDate.localeCompare(b.expiryDate) || a.id.localeCompare(b.id));
}

export function belowReorderLevel(levels: readonly StockLevel[], reorderLevels: Readonly<Record<string, number>>): StockLevel[] {
  return levels.filter((level) => {
    const threshold = reorderLevels[level.productId];
    return threshold !== undefined && level.available < threshold;
  });
}

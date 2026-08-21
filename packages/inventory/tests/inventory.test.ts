import { describe, expect, it } from 'vitest';
import { allocateFefo, availableStock, selectFefoBatches, type StockBatch } from '../src/index';

const batches: StockBatch[] = [
  { id: 'late', productId: 'p', lotNumber: 'L2', expiryDate: '2027-02-01', onHand: 10, reserved: 2, receivedAt: '2026-02-01' },
  { id: 'early', productId: 'p', lotNumber: 'L1', expiryDate: '2026-12-01', onHand: 5, reserved: 0, receivedAt: '2026-01-01' },
  { id: 'expired', productId: 'p', lotNumber: 'L0', expiryDate: '2026-01-01', onHand: 20, reserved: 0, receivedAt: '2025-01-01' },
];

describe('inventory', () => {
  it('calculates available stock and excludes expired batches', () => {
    expect(availableStock(batches[0]!)).toBe(8);
    expect(selectFefoBatches(batches, 3, '2026-08-21').map((batch) => batch.id)).toEqual(['early', 'late']);
  });
  it('allocates by FEFO and explicitly reports shortages', () => {
    expect(allocateFefo(batches, 12, '2026-08-21')).toEqual({ status: 'allocated', requested: 12, allocated: 12, remaining: 0, allocations: [{ batchId: 'early', quantity: 5 }, { batchId: 'late', quantity: 7 }] });
    expect(allocateFefo(batches, 30, '2026-08-21').status).toBe('partial');
  });
});

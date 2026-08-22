import { describe, expect, it } from 'vitest';
import { allocateFefo, applyStockMovement, availableStock, belowReorderLevel, expiringBatches, rebuildBalances, releaseReservation, reserveStock, selectFefoBatches, type StockBatch } from '../src/index';

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

describe('movement application', () => {
  const batch: StockBatch = batches[1]!;
  it('applies increases and decreases transactionally per batch', () => {
    expect(applyStockMovement(batch, { kind: 'purchase', quantity: 5 }, '2026-08-21').onHand).toBe(10);
    expect(applyStockMovement(batch, { kind: 'sale', quantity: 3 }, '2026-08-21').onHand).toBe(2);
    expect(applyStockMovement(batch, { kind: 'adjustment', quantity: -2 }, '2026-08-21').onHand).toBe(3);
  });
  it('never allows negative stock or sales from expired batches', () => {
    expect(() => applyStockMovement(batch, { kind: 'sale', quantity: 6 }, '2026-08-21')).toThrow('Insufficient');
    expect(() => applyStockMovement(batch, { kind: 'adjustment', quantity: -6 }, '2026-08-21')).toThrow('negative');
    expect(() => applyStockMovement(batches[2]!, { kind: 'sale', quantity: 1 }, '2026-08-21')).toThrow('expired');
    expect(() => applyStockMovement(batch, { kind: 'purchase', quantity: 0 }, '2026-08-21')).toThrow();
  });
});

describe('reservations', () => {
  it('reserves atomically by FEFO and releases back', () => {
    const reservation = reserveStock(batches, 7, '2026-08-21');
    expect(reservation.status).toBe('reserved');
    expect(reservation.allocations).toEqual([{ batchId: 'early', quantity: 5 }, { batchId: 'late', quantity: 2 }]);
    const released = releaseReservation(reservation.batches, reservation.allocations);
    expect(released.find((batch) => batch.id === 'early')?.reserved).toBe(0);
    expect(released.find((batch) => batch.id === 'late')?.reserved).toBe(2);
  });
  it('fails without partial reservation and guards over-release', () => {
    const failed = reserveStock(batches, 99, '2026-08-21');
    expect(failed.status).toBe('failed');
    expect(failed.batches).toEqual(batches);
    const reservation = reserveStock(batches, 5, '2026-08-21');
    expect(() => releaseReservation(reservation.batches, [{ batchId: 'early', quantity: 6 }])).toThrow('more than reserved');
  });
});

describe('ledger rebuild', () => {
  it('rebuilds balances from signed movements in deterministic order', () => {
    const movements = [
      { id: 'm1', batchId: 'b1', productId: 'p', quantity: 50, kind: 'purchase' as const, referenceId: 'po1', createdAt: '2026-01-01T00:00:00Z' },
      { id: 'm3', batchId: 'b1', productId: 'p', quantity: -5, kind: 'sale' as const, referenceId: 's1', createdAt: '2026-01-03T00:00:00Z' },
      { id: 'm2', batchId: 'b1', productId: 'p', quantity: 20, kind: 'purchase' as const, referenceId: 'po2', createdAt: '2026-01-02T00:00:00Z' },
    ];
    expect(rebuildBalances(movements, batches)).toEqual([{ productId: 'p', onHand: 65, reserved: 2 }]);
  });
  it('rejects ledgers that would drive stock negative', () => {
    expect(() => rebuildBalances([{ id: 'm1', batchId: 'b1', productId: 'p', quantity: -1, kind: 'sale', referenceId: 's', createdAt: '2026-01-01T00:00:00Z' }])).toThrow('negative');
  });
});

describe('stock queries', () => {
  it('lists batches expiring within a window and products below reorder level', () => {
    expect(expiringBatches([...batches, { ...batches[0]!, id: 'soon', expiryDate: '2026-09-15', onHand: 4 }], '2026-08-21', 30).map((batch) => batch.id)).toEqual(['soon']);
    expect(belowReorderLevel([{ productId: 'p', available: 13 }], { p: 20 })).toEqual([{ productId: 'p', available: 13 }]);
    expect(belowReorderLevel([{ productId: 'p', available: 25 }], { p: 20 })).toEqual([]);
  });
});

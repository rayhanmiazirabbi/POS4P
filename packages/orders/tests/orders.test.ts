import { describe, expect, it } from 'vitest';
import { transitionOrder, validateOrderToSale, type Order } from '../src/index';

const order: Order = { id: 'o1', customerId: null, status: 'placed', requiresPrescription: true, prescriptionApproved: false };
describe('orders', () => {
  it('enforces explicit state transitions and prescription gating', () => { expect(transitionOrder(order, 'accepted').order.status).toBe('accepted'); const preparing = { ...order, status: 'preparing' as const }; expect(() => transitionOrder(preparing, 'ready')).toThrow('Prescription'); expect(transitionOrder({ ...preparing, prescriptionApproved: true }, 'ready').order.status).toBe('ready'); });
  it('requires ready order and idempotency for sale conversion', () => { expect(() => validateOrderToSale({ ...order, status: 'ready' }, { orderId: 'o1', idempotencyKey: 'order-sale-confirm-1' })).not.toThrow(); expect(() => validateOrderToSale(order, { orderId: 'o1', idempotencyKey: 'order-sale-confirm-1' })).toThrow('ready'); });
});

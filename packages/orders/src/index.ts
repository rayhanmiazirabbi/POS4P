export type OrderStatus = 'draft' | 'placed' | 'accepted' | 'preparing' | 'ready' | 'completed' | 'cancelled';
export type Order = { id: string; customerId: string | null; status: OrderStatus; requiresPrescription: boolean; prescriptionApproved: boolean; reservationId?: string; };
export type OrderStatusChange = { orderId: string; from: OrderStatus; to: OrderStatus; changedAt: string; };
const transitions: Record<OrderStatus, readonly OrderStatus[]> = { draft: ['placed', 'cancelled'], placed: ['accepted', 'cancelled'], accepted: ['preparing', 'cancelled'], preparing: ['ready', 'cancelled'], ready: ['completed', 'cancelled'], completed: [], cancelled: [] };

export function canTransition(from: OrderStatus, to: OrderStatus): boolean { return transitions[from].includes(to); }
export function transitionOrder(order: Order, to: OrderStatus): { order: Order; change: OrderStatusChange } {
  if (!canTransition(order.status, to)) throw new Error(`Invalid order transition: ${order.status} -> ${to}`);
  if (to === 'ready' && order.requiresPrescription && !order.prescriptionApproved) throw new Error('Prescription approval is required');
  const change = { orderId: order.id, from: order.status, to, changedAt: new Date().toISOString() };
  return { order: { ...order, status: to }, change };
}

export type OrderToSaleRequest = { orderId: string; idempotencyKey: string };
export function validateOrderToSale(order: Order, request: OrderToSaleRequest): void { if (order.id !== request.orderId) throw new Error('Order does not match request'); if (order.status !== 'ready') throw new Error('Only ready orders can become sales'); if (request.idempotencyKey.trim().length < 16) throw new Error('Idempotency key is too short'); }

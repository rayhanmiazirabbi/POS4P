import { normalizePhone } from '@pharmacy/core';

export type Customer = { id: string; organizationId: string; displayName: string; phone: string; status: 'active' | 'inactive'; };
export type CustomerSummary = { customerId: string; due: string; saleCount: number; lastSaleAt: string | null };
export type CustomerAssociation = { customerId: string | null };

export function normalizeCustomerPhone(phone: string): string { return normalizePhone(phone); }
export function findCustomerByPhone(customers: readonly Customer[], phone: string): Customer | undefined { const normalized = normalizeCustomerPhone(phone); return customers.find((customer) => customer.phone === normalized && customer.status === 'active'); }
export function associateCustomer(customerId?: string | null): CustomerAssociation { return { customerId: customerId ?? null }; }
export function summarizeCustomer(customerId: string, sales: readonly { customerId: string | null; due: string; createdAt: string }[]): CustomerSummary {
  const customerSales = sales.filter((sale) => sale.customerId === customerId);
  return { customerId, due: customerSales.reduce((total, sale) => total + Number(sale.due), 0).toFixed(2), saleCount: customerSales.length, lastSaleAt: customerSales.map((sale) => sale.createdAt).sort().at(-1) ?? null };
}

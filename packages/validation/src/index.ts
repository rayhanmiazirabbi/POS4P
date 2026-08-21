import { normalizeBarcode, normalizePhone } from '@pharmacy/core';
import { z } from 'zod';

const decimal = z.string().regex(/^\d+(\.\d{1,2})?$/, 'Must be a non-negative decimal with up to 2 places');
const quantity = decimal.refine((value) => Number(value) > 0, 'Must be greater than zero');

export const productSearchSchema = z.object({
  query: z.string().trim().min(1).max(100),
  barcode: z.string().transform(normalizeBarcode).optional(),
  limit: z.coerce.number().int().min(1).max(100).default(25),
}).strict();

export const customerSchema = z.object({
  displayName: z.string().trim().min(1).max(160),
  phone: z.string().transform(normalizePhone),
}).strict();

export const cartLineSchema = z.object({
  storeProductId: z.string().uuid(), quantity, unitPrice: decimal,
}).strict();

export const saleSchema = z.object({
  customerId: z.string().uuid().nullable().default(null),
  lines: z.array(cartLineSchema).min(1),
  idempotencyKey: z.string().trim().min(16).max(128),
}).strict();

export const paymentSchema = z.object({
  method: z.enum(['cash', 'bkash', 'nagad', 'due']), amount: decimal,
}).strict().refine((value) => Number(value.amount) > 0, { message: 'Payment amount must be positive', path: ['amount'] });

export const userSchema = z.object({
  displayName: z.string().trim().min(1).max(160),
  role: z.enum(['owner', 'manager', 'cashier', 'inventory_staff']),
  phone: z.string().transform(normalizePhone),
}).strict();

export const purchaseSchema = z.object({
  supplierId: z.string().uuid(), invoiceNumber: z.string().trim().min(1).max(100),
  items: z.array(z.object({ storeProductId: z.string().uuid(), quantity, unitCost: decimal, expiryDate: z.string().date() }).strict()).min(1),
  idempotencyKey: z.string().trim().min(16).max(128),
}).strict();

export type SaleInput = z.infer<typeof saleSchema>;

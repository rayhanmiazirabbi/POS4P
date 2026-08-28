import { money } from '@pharmacy/money';
import { provisionalReceipt } from '@pharmacy/sales';
import { describe, expect, it, vi } from 'vitest';

const { storage } = vi.hoisted(() => ({ storage: new Map<string, string>() }));
vi.mock('../platform/dexie', () => ({
  dexieStorage: {
    get: async (key: string) => storage.get(key) ?? null,
    set: async (key: string, value: string) => { storage.set(key, value); },
    remove: async (key: string) => { storage.delete(key); },
  },
}));

import {
  defaultReceiptConfig,
  cacheReceiptConfig,
  formatConfiguredReceiptText,
  formatReceiptDate,
  receiptConfigFromSettings,
  readCachedReceiptConfig,
  receiptPageCss,
  validReceiptWidth,
  type PrintableReceipt,
} from './receipt';

function printable(config = defaultReceiptConfig): PrintableReceipt {
  return {
    receipt: provisionalReceipt({
      organizationName: 'Care Pharmacy', storeName: 'Uttara', customerName: 'Ayesha', issuedAt: '2026-08-28T10:30:00Z',
      lines: [{ id: 'line-1', productId: 'product-1', name: 'Paracetamol', quantity: 2, unitPrice: money('12.50'), discount: money('0.00'), tax: money('0.00') }],
      payments: [{ method: 'cash', amount: money('25.00'), receivedAmount: money('50.00') }],
    }),
    config,
    cashierName: 'Nadia',
    locale: 'en-US',
    timezone: 'UTC',
  };
}

describe('receipt configuration', () => {
  it('uses the organization footer only when the branch footer is empty', () => {
    expect(receiptConfigFromSettings({ receiptFooter: null }, 'Organization footer').footer).toBe('Organization footer');
    expect(receiptConfigFromSettings({ receiptFooter: 'Branch footer' }, 'Organization footer').footer).toBe('Branch footer');
  });

  it('normalizes older settings responses onto printable defaults', () => {
    const config = receiptConfigFromSettings({ printReceiptByDefault: false, receiptPaperWidthMm: 12 });
    expect(config.paperWidthMm).toBe(80);
    expect(config.printByDefault).toBe(false);
    expect(config.showTotal).toBe(true);
  });

  it('honors visibility choices in copied receipt text', () => {
    const text = formatConfiguredReceiptText(printable({
      ...defaultReceiptConfig,
      showBusinessName: false,
      showStoreName: false,
      showReceiptNumber: false,
      showDateTime: false,
      showCustomer: false,
      showCashier: false,
      showItems: false,
      showSubtotal: false,
      showDiscounts: false,
      showCharges: false,
      showTotal: false,
      showPayments: false,
      showCashReceived: false,
      showChangeDue: false,
    }));
    expect(text).toBe('');
  });

  it('formats the configured transaction details and local date', () => {
    const text = formatConfiguredReceiptText(printable());
    expect(text).toContain('Care Pharmacy');
    expect(text).toContain('Paracetamol  x2  @ ৳12.50  ৳25.00');
    expect(text).toContain('CASH RECEIVED ৳50.00');
    expect(text).toContain('CHANGE ৳25.00');
    expect(formatReceiptDate('2026-08-28T10:30:00Z', 'en-US', 'UTC')).toContain('2026');
  });

  it('bounds custom widths and emits thermal or A4 page rules', () => {
    expect(validReceiptWidth(48)).toBe(true);
    expect(validReceiptWidth(210)).toBe(true);
    expect(validReceiptWidth(47)).toBe(false);
    expect(receiptPageCss(80)).toContain('80mm auto');
    expect(receiptPageCss(210)).toContain('A4 portrait');
  });

  it('caches the effective branch configuration for offline receipts', async () => {
    const configured = { ...defaultReceiptConfig, paperWidthMm: 58, footer: 'Come again' };
    await cacheReceiptConfig('org-1', 'store-1', configured);
    await expect(readCachedReceiptConfig('org-1', 'store-1')).resolves.toMatchObject({ paperWidthMm: 58, footer: 'Come again' });
  });
});

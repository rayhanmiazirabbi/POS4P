'use client';

import type { OrganizationSettings, StoreSettings, StoreSettingsUpdate } from '@pharmacy/api';
import type { Receipt } from '@pharmacy/sales';

import { dexieStorage } from '../platform/dexie';

export const MIN_RECEIPT_WIDTH_MM = 48;
export const MAX_RECEIPT_WIDTH_MM = 210;

export type ReceiptConfig = {
  header: string | null;
  footer: string | null;
  logo: string | null;
  businessName: string | null;
  address: string | null;
  phone: string | null;
  email: string | null;
  taxId: string | null;
  paperWidthMm: number;
  printByDefault: boolean;
  showLogo: boolean;
  showBusinessName: boolean;
  showStoreName: boolean;
  showContactDetails: boolean;
  showHeader: boolean;
  showReceiptNumber: boolean;
  showDateTime: boolean;
  showCustomer: boolean;
  showCashier: boolean;
  showItems: boolean;
  showItemQuantity: boolean;
  showUnitPrice: boolean;
  showLineTotal: boolean;
  showSubtotal: boolean;
  showDiscounts: boolean;
  showCharges: boolean;
  showTotal: boolean;
  showPayments: boolean;
  showCashReceived: boolean;
  showChangeDue: boolean;
  showFooter: boolean;
};

export type PrintableReceipt = {
  receipt: Receipt;
  config: ReceiptConfig;
  cashierName: string | null;
  locale: string;
  timezone: string;
};

export const defaultReceiptConfig: ReceiptConfig = Object.freeze({
  header: null,
  footer: null,
  logo: null,
  businessName: null,
  address: null,
  phone: null,
  email: null,
  taxId: null,
  paperWidthMm: 80,
  printByDefault: true,
  showLogo: true,
  showBusinessName: true,
  showStoreName: true,
  showContactDetails: true,
  showHeader: true,
  showReceiptNumber: true,
  showDateTime: true,
  showCustomer: true,
  showCashier: true,
  showItems: true,
  showItemQuantity: true,
  showUnitPrice: true,
  showLineTotal: true,
  showSubtotal: true,
  showDiscounts: true,
  showCharges: true,
  showTotal: true,
  showPayments: true,
  showCashReceived: true,
  showChangeDue: true,
  showFooter: true,
});

function clean(value: string | null | undefined): string | null {
  const trimmed = value?.trim();
  return trimmed ? trimmed : null;
}

function bool(value: boolean | undefined, fallback: boolean): boolean {
  return typeof value === 'boolean' ? value : fallback;
}

export function validReceiptWidth(value: number): boolean {
  return Number.isInteger(value) && value >= MIN_RECEIPT_WIDTH_MM && value <= MAX_RECEIPT_WIDTH_MM;
}

/** Coerce an API response or cached older response onto today's complete defaults. */
export function receiptConfigFromSettings(settings?: Partial<StoreSettings> | null, organizationFooter?: string | null): ReceiptConfig {
  const width = Number(settings?.receiptPaperWidthMm);
  return {
    header: clean(settings?.receiptHeader),
    footer: clean(settings?.receiptFooter) ?? clean(organizationFooter),
    logo: clean(settings?.receiptLogo),
    businessName: clean(settings?.receiptBusinessName),
    address: clean(settings?.receiptAddress),
    phone: clean(settings?.receiptPhone),
    email: clean(settings?.receiptEmail),
    taxId: clean(settings?.receiptTaxId),
    paperWidthMm: validReceiptWidth(width) ? width : defaultReceiptConfig.paperWidthMm,
    printByDefault: bool(settings?.printReceiptByDefault, defaultReceiptConfig.printByDefault),
    showLogo: bool(settings?.receiptShowLogo, true),
    showBusinessName: bool(settings?.receiptShowBusinessName, true),
    showStoreName: bool(settings?.receiptShowStoreName, true),
    showContactDetails: bool(settings?.receiptShowContactDetails, true),
    showHeader: bool(settings?.receiptShowHeader, true),
    showReceiptNumber: bool(settings?.receiptShowReceiptNumber, true),
    showDateTime: bool(settings?.receiptShowDateTime, true),
    showCustomer: bool(settings?.receiptShowCustomer, true),
    showCashier: bool(settings?.receiptShowCashier, true),
    showItems: bool(settings?.receiptShowItems, true),
    showItemQuantity: bool(settings?.receiptShowItemQuantity, true),
    showUnitPrice: bool(settings?.receiptShowUnitPrice, true),
    showLineTotal: bool(settings?.receiptShowLineTotal, true),
    showSubtotal: bool(settings?.receiptShowSubtotal, true),
    showDiscounts: bool(settings?.receiptShowDiscounts, true),
    showCharges: bool(settings?.receiptShowCharges, true),
    showTotal: bool(settings?.receiptShowTotal, true),
    showPayments: bool(settings?.receiptShowPayments, true),
    showCashReceived: bool(settings?.receiptShowCashReceived, true),
    showChangeDue: bool(settings?.receiptShowChangeDue, true),
    showFooter: bool(settings?.receiptShowFooter, true),
  };
}

export function receiptSettingsPatch(config: ReceiptConfig, branchFooter: string | null): StoreSettingsUpdate {
  return {
    receiptHeader: clean(config.header),
    receiptFooter: clean(branchFooter),
    receiptLogo: clean(config.logo),
    receiptBusinessName: clean(config.businessName),
    receiptAddress: clean(config.address),
    receiptPhone: clean(config.phone),
    receiptEmail: clean(config.email),
    receiptTaxId: clean(config.taxId),
    receiptPaperWidthMm: config.paperWidthMm,
    printReceiptByDefault: config.printByDefault,
    receiptShowLogo: config.showLogo,
    receiptShowBusinessName: config.showBusinessName,
    receiptShowStoreName: config.showStoreName,
    receiptShowContactDetails: config.showContactDetails,
    receiptShowHeader: config.showHeader,
    receiptShowReceiptNumber: config.showReceiptNumber,
    receiptShowDateTime: config.showDateTime,
    receiptShowCustomer: config.showCustomer,
    receiptShowCashier: config.showCashier,
    receiptShowItems: config.showItems,
    receiptShowItemQuantity: config.showItemQuantity,
    receiptShowUnitPrice: config.showUnitPrice,
    receiptShowLineTotal: config.showLineTotal,
    receiptShowSubtotal: config.showSubtotal,
    receiptShowDiscounts: config.showDiscounts,
    receiptShowCharges: config.showCharges,
    receiptShowTotal: config.showTotal,
    receiptShowPayments: config.showPayments,
    receiptShowCashReceived: config.showCashReceived,
    receiptShowChangeDue: config.showChangeDue,
    receiptShowFooter: config.showFooter,
  };
}

export function receiptCacheKey(organizationId: string, storeId: string): string {
  return `receipt-settings:${organizationId}:${storeId}`;
}

export async function cacheReceiptConfig(organizationId: string, storeId: string, config: ReceiptConfig): Promise<void> {
  await dexieStorage.set(receiptCacheKey(organizationId, storeId), JSON.stringify(config));
}

export async function readCachedReceiptConfig(organizationId: string, storeId: string): Promise<ReceiptConfig | null> {
  const raw = await dexieStorage.get(receiptCacheKey(organizationId, storeId));
  if (!raw) return null;
  try {
    const parsed = JSON.parse(raw) as Partial<ReceiptConfig>;
    const width = Number(parsed.paperWidthMm);
    return {
      ...defaultReceiptConfig,
      ...parsed,
      paperWidthMm: validReceiptWidth(width) ? width : defaultReceiptConfig.paperWidthMm,
    };
  } catch {
    return null;
  }
}

export async function loadEffectiveReceiptConfig(
  organizationId: string,
  storeId: string,
  fetchSettings: () => Promise<{ store: StoreSettings; organization: OrganizationSettings }>,
): Promise<ReceiptConfig> {
  try {
    const { store, organization } = await fetchSettings();
    const config = receiptConfigFromSettings(store, organization.receiptFooter);
    await cacheReceiptConfig(organizationId, storeId, config);
    return config;
  } catch (cause) {
    const cached = await readCachedReceiptConfig(organizationId, storeId);
    if (cached) return cached;
    if (!navigator.onLine) return defaultReceiptConfig;
    throw cause;
  }
}

export function formatReceiptDate(issuedAt: string, locale: string, timezone: string): string {
  try {
    return new Intl.DateTimeFormat(locale || 'en-BD', {
      timeZone: timezone || 'Asia/Dhaka',
      dateStyle: 'medium',
      timeStyle: 'short',
    }).format(new Date(issuedAt));
  } catch {
    return issuedAt;
  }
}

function amountIsZero(value?: { amount: string }): boolean {
  return !value || Number(value.amount) === 0;
}

function dueTender(receipt: Receipt): number {
  return receipt.payments
    .filter((payment) => payment.method === 'due')
    .reduce((total, payment) => total + Number(payment.amount.amount), 0);
}

export function formatConfiguredReceiptText(printable: PrintableReceipt): string {
  const { receipt, config } = printable;
  const lines: string[] = [];
  if (config.showBusinessName) lines.push(config.businessName ?? receipt.organizationName);
  if (config.showStoreName) lines.push(receipt.storeName);
  if (config.showContactDetails) {
    lines.push(...[config.address, config.phone, config.email, config.taxId].filter((value): value is string => Boolean(value)));
  }
  if (config.showHeader && config.header) lines.push(config.header);
  if (lines.length > 0) lines.push('');
  if (config.showReceiptNumber) lines.push(receipt.receiptNumber === null ? 'RECEIPT PENDING UPLOAD' : `Receipt ${receipt.receiptNumber}`);
  if (config.showDateTime) lines.push(formatReceiptDate(receipt.issuedAt, printable.locale, printable.timezone));
  if (config.showCustomer && receipt.customerName) lines.push(`Customer: ${receipt.customerName}`);
  if (config.showCashier && printable.cashierName) lines.push(`Cashier: ${printable.cashierName}`);
  if (config.showItems) {
    if (lines.length > 0) lines.push('');
    for (const item of receipt.lines) {
      const details = [item.name];
      if (config.showItemQuantity) details.push(`x${item.quantity}`);
      if (config.showUnitPrice) details.push(`@ ৳${item.unitPrice.amount}`);
      if (config.showLineTotal) details.push(`৳${item.lineTotal.amount}`);
      lines.push(details.join('  '));
    }
  }
  if (config.showSubtotal) lines.push(`SUBTOTAL ৳${receipt.totals.subtotal.amount}`);
  if (config.showDiscounts && !amountIsZero(receipt.totals.discount)) lines.push(`DISCOUNT -৳${receipt.totals.discount.amount}`);
  if (config.showCharges && !amountIsZero(receipt.deliveryCharge)) lines.push(`DELIVERY ৳${receipt.deliveryCharge?.amount}`);
  if (config.showCharges && !amountIsZero(receipt.otherFee)) lines.push(`${(receipt.otherFeeLabel ?? 'OTHER FEE').toUpperCase()} ৳${receipt.otherFee?.amount}`);
  if (config.showTotal) lines.push(`TOTAL ৳${receipt.totals.total.amount}`);
  if (config.showPayments) {
    if (!amountIsZero(receipt.advanceApplied)) lines.push(`ADVANCE APPLIED ৳${receipt.advanceApplied?.amount}${receipt.advanceReference ? ` (${receipt.advanceReference})` : ''}`);
    for (const payment of receipt.payments) {
      if (payment.method !== 'due') lines.push(`${payment.method.toUpperCase()} ৳${payment.amount.amount}`);
    }
  }
  if (config.showCashReceived) {
    for (const payment of receipt.payments) {
      if (payment.method === 'cash' && payment.receivedAmount) lines.push(`CASH RECEIVED ৳${payment.receivedAmount.amount}`);
    }
  }
  if (config.showChangeDue && !amountIsZero(receipt.change)) lines.push(`CHANGE ৳${receipt.change.amount}`);
  const due = dueTender(receipt);
  if (config.showChangeDue && due > 0) lines.push(`DUE ৳${due.toFixed(2)}`);
  if (config.showFooter && config.footer) lines.push('', config.footer);
  return lines.join('\n');
}

export function receiptPageCss(widthMm: number): string {
  const width = validReceiptWidth(widthMm) ? widthMm : defaultReceiptConfig.paperWidthMm;
  return width === 210
    ? '@page { size: A4 portrait; margin: 12mm; }'
    : `@page { size: ${width}mm auto; margin: 0; }`;
}

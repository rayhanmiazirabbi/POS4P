'use client';

import type { PurchaseReceipt } from '@pharmacy/api';
import { useEffect, useRef, useState, type CSSProperties, type ReactNode } from 'react';

import { formatReceiptDate, receiptPageCss, type ReceiptConfig } from '@/lib/receipt';

export type PrintablePurchaseReceipt = {
  receipt: PurchaseReceipt;
  config: ReceiptConfig;
  organizationName: string;
  storeName: string;
  staffName: string | null;
  locale: string;
  timezone: string;
};

function receiptText(printable: PrintablePurchaseReceipt): string {
  const { receipt } = printable;
  const lines = [
    printable.config.businessName ?? printable.organizationName,
    printable.storeName,
    '',
    `GOODS RECEIVED ${receipt.receiptNumber}`,
    `Supplier: ${receipt.supplierName}`,
    ...(receipt.invoiceNumber ? [`Supplier invoice: ${receipt.invoiceNumber}`] : []),
    formatReceiptDate(receipt.confirmedAt, printable.locale, printable.timezone),
    ...(printable.staffName ? [`Received by: ${printable.staffName}`] : []),
    '',
    ...receipt.lines.map((line) => `${line.name} [${line.batchNumber}]  ${line.quantity} x ৳${line.unitCost}  ৳${line.lineTotal}${line.expiryDate ? `  exp ${line.expiryDate}` : ''}`),
    `TOTAL ৳${receipt.totalAmount}`,
    ...receipt.payments.map((payment) => `${payment.method.toUpperCase()} ৳${payment.amount}${payment.providerReference ? ` (${payment.providerReference})` : ''}`),
    `CREDIT ৳${receipt.creditAmount}`,
    `SUPPLIER BALANCE ৳${receipt.supplierBalanceAfter}`,
  ];
  return lines.join('\n');
}

function PurchaseReceiptDocument({ printable }: { printable: PrintablePurchaseReceipt }): ReactNode {
  const { receipt, config } = printable;
  const thermal = config.paperWidthMm < 150;
  const style = { '--receipt-width': `${config.paperWidthMm}mm` } as CSSProperties;
  return <article className={`receipt-sheet ${thermal ? 'receipt-sheet--thermal' : 'receipt-sheet--page'} receipt-print-root`} style={style} aria-label="Goods received voucher">
    <style media="print">{receiptPageCss(config.paperWidthMm)}</style>
    <header className="receipt-brand">
      {config.showLogo && config.logo && <img className="receipt-logo" src={config.logo} alt="" />}
      <h2>{config.businessName ?? printable.organizationName}</h2>
      <p>{printable.storeName}</p>
      <p className="receipt-custom-copy">Goods received voucher</p>
    </header>
    <section className="receipt-meta">
      <p><span>GRN</span><strong>{receipt.receiptNumber}</strong></p>
      <p><span>Date</span><strong>{formatReceiptDate(receipt.confirmedAt, printable.locale, printable.timezone)}</strong></p>
      <p><span>Supplier</span><strong>{receipt.supplierName}</strong></p>
      {receipt.invoiceNumber && <p><span>Invoice</span><strong>{receipt.invoiceNumber}</strong></p>}
      {printable.staffName && <p><span>Received by</span><strong>{printable.staffName}</strong></p>}
    </section>
    <section className="receipt-items">
      {receipt.lines.map((line) => <div className="receipt-item purchase-receipt-item" key={line.purchaseItemId}>
        <strong>{line.name}</strong><small>{line.sku} · Batch {line.batchNumber}{line.expiryDate ? ` · Exp ${line.expiryDate}` : ''}</small>
        <span className="receipt-item-math"><span>{line.quantity}×</span><span>৳{line.unitCost}</span><b>৳{line.lineTotal}</b></span>
      </div>)}
    </section>
    <section className="receipt-totals">
      <p className="receipt-row receipt-row--total"><span>Total</span><strong>৳{receipt.totalAmount}</strong></p>
      {receipt.payments.map((payment) => <p className="receipt-row" key={payment.method}><span>{payment.method}{payment.providerReference && <small>{payment.providerReference}</small>}</span><strong>৳{payment.amount}</strong></p>)}
      <p className="receipt-row receipt-row--emphasis"><span>Credit on receipt</span><strong>৳{receipt.creditAmount}</strong></p>
      <p className="receipt-row receipt-row--emphasis"><span>Supplier balance</span><strong>৳{receipt.supplierBalanceAfter}</strong></p>
    </section>
    {config.showFooter && config.footer && <footer className="receipt-footer">{config.footer}</footer>}
  </article>;
}

export function PurchaseReceiptDialog({ printable, onClose }: { printable: PrintablePurchaseReceipt; onClose: () => void }): ReactNode {
  const root = useRef<HTMLDivElement>(null);
  const [status, setStatus] = useState('');
  useEffect(() => { const onKey = (event: KeyboardEvent): void => { if (event.key === 'Escape') onClose(); }; window.addEventListener('keydown', onKey); return () => window.removeEventListener('keydown', onKey); }, [onClose]);
  useEffect(() => { if (!printable.config.printByDefault) return; const timer = window.setTimeout(() => window.print(), 100); return () => window.clearTimeout(timer); }, [printable]);
  async function copy(): Promise<void> { try { await navigator.clipboard.writeText(receiptText(printable)); setStatus('Voucher text copied.'); } catch { setStatus('Could not copy voucher text.'); } }
  return <div className="receipt-dialog-backdrop"><div ref={root} className="receipt-dialog" role="dialog" aria-modal="true" aria-labelledby="purchase-receipt-title">
    <header className="receipt-dialog-header"><div><span className="eyebrow">Stock received</span><h2 id="purchase-receipt-title">{printable.receipt.receiptNumber}</h2><p>Ready to print or copy.</p></div><button type="button" className="icon-action" aria-label="Close voucher" onClick={onClose}>×</button></header>
    <div className="receipt-dialog-scroll"><div className="receipt-preview-canvas"><PurchaseReceiptDocument printable={printable} /></div></div>
    <footer className="receipt-dialog-actions"><span role="status">{status}</span><div><button type="button" className="quiet-action" onClick={() => void copy()}>Copy text</button><button type="button" className="quiet-action" onClick={() => window.print()}>Print again</button><button type="button" className="primary-action" onClick={onClose}>New receipt</button></div></footer>
  </div></div>;
}

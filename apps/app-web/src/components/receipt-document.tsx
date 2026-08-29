'use client';

import { useEffect, useState, type CSSProperties, type ReactNode } from 'react';

import type { PrintableReceipt } from '@/lib/receipt';
import { formatReceiptDate, receiptPageCss } from '@/lib/receipt';

export function ReceiptDocument({ printable, className = '' }: { printable: PrintableReceipt; className?: string }): ReactNode {
  const { receipt, config } = printable;
  const [logoFailed, setLogoFailed] = useState(false);
  useEffect(() => setLogoFailed(false), [config.logo]);
  const due = receipt.payments.filter((payment) => payment.method === 'due').reduce((sum, payment) => sum + Number(payment.amount.amount), 0);
  const thermal = config.paperWidthMm < 150;
  const style = { '--receipt-width': `${config.paperWidthMm}mm` } as CSSProperties;

  return (
    <article className={`receipt-sheet ${thermal ? 'receipt-sheet--thermal' : 'receipt-sheet--page'} ${className}`.trim()} style={style} aria-label="Sale receipt">
      <style media="print">{receiptPageCss(config.paperWidthMm)}</style>
      <header className="receipt-brand">
        {config.showLogo && config.logo && !logoFailed && <img className="receipt-logo" src={config.logo} alt="" referrerPolicy="no-referrer" onError={() => setLogoFailed(true)} />}
        {config.showBusinessName && <h2>{config.businessName ?? receipt.organizationName}</h2>}
        {config.showStoreName && <p>{receipt.storeName}</p>}
        {config.showContactDetails && <div className="receipt-contact">
          {config.address && <p>{config.address}</p>}
          {config.phone && <p>{config.phone}</p>}
          {config.email && <p>{config.email}</p>}
          {config.taxId && <p>Tax/VAT: {config.taxId}</p>}
        </div>}
        {config.showHeader && config.header && <p className="receipt-custom-copy">{config.header}</p>}
      </header>

      {(config.showReceiptNumber || config.showDateTime || (config.showCustomer && receipt.customerName) || (config.showCashier && printable.cashierName)) && <section className="receipt-meta">
        {config.showReceiptNumber && <p><span>Receipt</span><strong>{receipt.receiptNumber ?? 'Pending upload'}</strong></p>}
        {config.showDateTime && <p><span>Date</span><strong>{formatReceiptDate(receipt.issuedAt, printable.locale, printable.timezone)}</strong></p>}
        {config.showCustomer && receipt.customerName && <p><span>Customer</span><strong>{receipt.customerName}</strong></p>}
        {config.showCashier && printable.cashierName && <p><span>Cashier</span><strong>{printable.cashierName}</strong></p>}
      </section>}

      {config.showItems && <section className="receipt-items">
        {receipt.lines.map((line, index) => <div className="receipt-item" key={`${line.name}-${index}`}>
          <strong>{line.name}</strong>
          <span className="receipt-item-math">
            {config.showItemQuantity && <span>{line.quantity}×</span>}
            {config.showUnitPrice && <span>৳{line.unitPrice.amount}</span>}
            {config.showLineTotal && <b>৳{line.lineTotal.amount}</b>}
          </span>
        </div>)}
      </section>}

      <section className="receipt-totals">
        {config.showSubtotal && <ReceiptRow label="Subtotal" amount={receipt.totals.subtotal.amount} />}
        {config.showDiscounts && Number(receipt.totals.discount.amount) !== 0 && <ReceiptRow label="Discount" amount={`-${receipt.totals.discount.amount}`} />}
        {config.showCharges && Number(receipt.deliveryCharge?.amount ?? 0) !== 0 && <ReceiptRow label="Delivery" amount={receipt.deliveryCharge?.amount ?? '0.00'} />}
        {config.showCharges && Number(receipt.otherFee?.amount ?? 0) !== 0 && <ReceiptRow label={receipt.otherFeeLabel ?? 'Other fee'} amount={receipt.otherFee?.amount ?? '0.00'} />}
        {config.showTotal && <ReceiptRow label="Total" amount={receipt.totals.total.amount} total />}
        {config.showPayments && Number(receipt.advanceApplied?.amount ?? 0) !== 0 && <ReceiptRow label="Advance applied" amount={`-${receipt.advanceApplied?.amount ?? '0.00'}`} {...(receipt.advanceReference === undefined ? {} : { note: receipt.advanceReference })} />}
        {config.showPayments && receipt.loyaltyPointsRedeemed !== undefined && receipt.loyaltyCredit !== undefined && (
          <ReceiptRow label="Points redeemed" amount={`-${receipt.loyaltyCredit.amount}`} note={`${receipt.loyaltyPointsRedeemed} pts`} />
        )}
        {config.showPayments && receipt.loyaltyPointsRedeemed !== undefined && receipt.loyaltyBalanceAfter != null && (
          <ReceiptRow label="Points balance" amount={`${receipt.loyaltyBalanceAfter} pts`} currency={false} />
        )}
        {config.showPayments && receipt.payments.map((payment, index) => payment.method !== 'due' ? <ReceiptRow key={`${payment.method}-${index}`} label={payment.method} amount={payment.amount.amount} /> : null)}
        {config.showCashReceived && receipt.payments.map((payment, index) => payment.method === 'cash' && payment.receivedAmount
          ? <ReceiptRow key={`received-${index}`} label="Cash received" amount={payment.receivedAmount.amount} />
          : null)}
        {config.showChangeDue && Number(receipt.change.amount) !== 0 && <ReceiptRow label="Change" amount={receipt.change.amount} emphasis />}
        {config.showChangeDue && due > 0 && <ReceiptRow label="Due" amount={due.toFixed(2)} emphasis />}
      </section>

      {config.showFooter && config.footer && <footer className="receipt-footer">{config.footer}</footer>}
    </article>
  );
}

function ReceiptRow({ label, amount, total = false, emphasis = false, note, currency = true }: { label: string; amount: string; total?: boolean; emphasis?: boolean; note?: string | null; currency?: boolean }): ReactNode {
  return <p className={total ? 'receipt-row receipt-row--total' : emphasis ? 'receipt-row receipt-row--emphasis' : 'receipt-row'}>
    <span>{label}{note ? <small>{note}</small> : null}</span><strong>{currency ? `৳${amount}` : amount}</strong>
  </p>;
}

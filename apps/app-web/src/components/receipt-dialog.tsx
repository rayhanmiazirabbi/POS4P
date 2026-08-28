'use client';

import { useEffect, useRef, useState, type ReactNode } from 'react';

import { formatConfiguredReceiptText, type PrintableReceipt } from '@/lib/receipt';

import { ReceiptDocument } from './receipt-document';

async function waitForReceiptImages(root: HTMLElement): Promise<void> {
  const images = Array.from(root.querySelectorAll('img'));
  if (images.length === 0) return;
  await Promise.race([
    Promise.all(images.map(async (image) => {
      if (image.complete) return;
      try { await image.decode(); } catch { /* A failed logo is hidden by ReceiptDocument. */ }
    })),
    new Promise<void>((resolve) => window.setTimeout(resolve, 2000)),
  ]);
}

function nextPaint(): Promise<void> {
  return new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => resolve())));
}

export function ReceiptDialog({ printable, onClose }: { printable: PrintableReceipt; onClose: () => void }): ReactNode {
  const dialogRef = useRef<HTMLDivElement>(null);
  const printedKey = useRef<string | null>(null);
  const [copyStatus, setCopyStatus] = useState('');
  const key = printable.receipt.saleId ?? `${printable.receipt.issuedAt}:${printable.receipt.receiptNumber ?? 'pending'}`;

  async function print(): Promise<void> {
    if (dialogRef.current) await waitForReceiptImages(dialogRef.current);
    await nextPaint();
    window.print();
  }

  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    dialogRef.current?.querySelector<HTMLButtonElement>('button')?.focus();
    const onKeyDown = (event: KeyboardEvent): void => {
      if (event.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onKeyDown);
    return () => { window.removeEventListener('keydown', onKeyDown); previous?.focus(); };
  }, [onClose]);

  useEffect(() => {
    if (!printable.config.printByDefault || printedKey.current === key) return;
    printedKey.current = key;
    let cancelled = false;
    const timer = window.setTimeout(() => { if (!cancelled) void print(); }, 80);
    return () => { cancelled = true; window.clearTimeout(timer); };
  }, [key, printable]);

  async function copy(): Promise<void> {
    try {
      await navigator.clipboard.writeText(formatConfiguredReceiptText(printable));
      setCopyStatus('Receipt text copied.');
    } catch {
      setCopyStatus('Could not copy receipt text.');
    }
  }

  return <div className="receipt-dialog-backdrop" role="presentation">
    <div className="receipt-dialog" ref={dialogRef} role="dialog" aria-modal="true" aria-labelledby="receipt-dialog-title">
      <header className="receipt-dialog-header"><div><span className="eyebrow">Sale complete</span><h2 id="receipt-dialog-title">{printable.receipt.receiptNumber ? `Receipt ${printable.receipt.receiptNumber}` : 'Receipt pending upload'}</h2><p>{printable.receipt.receiptNumber ? 'Ready to print or copy.' : 'This queued sale receives its number after upload.'}</p></div><button type="button" className="icon-action" aria-label="Close receipt" onClick={onClose}>×</button></header>
      <div className="receipt-dialog-scroll"><div className="receipt-preview-canvas"><ReceiptDocument printable={printable} className="receipt-print-root" /></div></div>
      <footer className="receipt-dialog-actions"><span role="status">{copyStatus}</span><div><button type="button" className="quiet-action" onClick={() => void copy()}>Copy text</button><button type="button" className="quiet-action" onClick={() => void print()}>Print again</button><button type="button" className="primary-action" onClick={onClose}>New sale</button></div></footer>
    </div>
  </div>;
}

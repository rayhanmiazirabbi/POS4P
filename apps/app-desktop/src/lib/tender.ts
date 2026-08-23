import { tenderPayments, type DigitalMethod, type Payment, type TenderSplit } from '@pharmacy/sales';

/** The wallet tenders this till offers beside cash, in button order. */
export const digitalMethods = ['bkash', 'nagad'] as const;

export type DigitalMethodChoice = (typeof digitalMethods)[number];

export const defaultDigitalMethod: DigitalMethodChoice = 'bkash';

const digitalLabels: Record<DigitalMethodChoice, string> = { bkash: 'bKash', nagad: 'Nagad' };

export function digitalLabel(method: DigitalMethodChoice): string {
  return digitalLabels[method];
}

/**
 * The payment rows a sale posts with.
 *
 * The chosen method names only the wallet portion of the split; cash rows stay
 * `cash` no matter what is selected here, so a pure-cash sale is never recorded
 * under bKash or Nagad. Rows for tenders of nothing are left out entirely --
 * see `tenderPayments`.
 */
export function buildPayments(split: TenderSplit, method: DigitalMethodChoice): Payment[] {
  return tenderPayments(split, method satisfies DigitalMethod);
}

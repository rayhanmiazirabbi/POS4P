/** Keep a non-negative decimal field editable while rejecting keyboard noise. */
export function decimalEntry(value: string): string {
  const cleaned = value.replace(/[^\d.]/g, '');
  const [whole = '', ...fractionParts] = cleaned.split('.');
  if (fractionParts.length === 0) return whole;
  return `${whole || '0'}.${fractionParts.join('')}`;
}

import { v7 as uuidv7, validate as validateUuid } from 'uuid';

export type UUID = string & { readonly __brand: 'UUID' };
export type ISODateTime = string & { readonly __brand: 'ISODateTime' };

export function createId(): UUID { return uuidv7() as UUID; }

export function assertId(value: string): UUID {
  if (!validateUuid(value)) throw new Error('Invalid UUID');
  return value as UUID;
}

export function nowUtc(): ISODateTime { return new Date().toISOString() as ISODateTime; }

export function normalizePhone(value: string): string {
  const compact = value.trim().replace(/[\s().-]/g, '');
  if (compact.startsWith('+')) return `+${compact.slice(1).replace(/\D/g, '')}`;
  const digits = compact.replace(/\D/g, '');
  return digits.startsWith('0') ? `+880${digits.slice(1)}` : `+880${digits}`;
}

export function normalizeBarcode(value: string): string { return value.trim().replace(/\s+/g, ''); }

export type Result<T, E = DomainError> = { ok: true; value: T } | { ok: false; error: E };

export type DomainErrorCode =
  | 'VALIDATION_ERROR' | 'UNAUTHORIZED' | 'FORBIDDEN' | 'NOT_FOUND' | 'CONFLICT'
  | 'IDEMPOTENCY_CONFLICT' | 'INSUFFICIENT_STOCK' | 'RATE_LIMITED' | 'INTERNAL_ERROR';

export type DomainError = {
  code: DomainErrorCode;
  message: string;
  field?: string;
  details?: Record<string, unknown>;
};

/** Build a `DomainError` without hand-rolling the object at every call site. */
export function domainError(
  code: DomainErrorCode,
  message: string,
  details?: Record<string, unknown>,
): DomainError {
  return details === undefined ? { code, message } : { code, message, details };
}

/** Structural guard: anything shaped like a `DomainError` counts, whatever class built it. */
export function isDomainError(value: unknown): value is DomainError {
  if (typeof value !== 'object' || value === null) return false;
  const candidate = value as Partial<DomainError>;
  return typeof candidate.code === 'string' && typeof candidate.message === 'string';
}

export type EventMetadata = {
  eventId: UUID;
  eventType: string;
  organizationId: UUID;
  storeId?: UUID;
  userId?: UUID;
  deviceId?: UUID;
  createdAt: ISODateTime;
  idempotencyKey?: string;
};

/**
 * Quantities are not money: stock counts and dosages keep 4 decimal places
 * (a half strip, 2.5 mg), matching `quantity_column()` on the backend. Money
 * helpers live in `@pharmacy/money` and stop at 2.
 */
export type Quantity = { value: string; unit: string };

const QUANTITY_PATTERN = /^\d+(\.\d{1,4})?$/;

export function parseQuantity(value: string): Quantity {
  const trimmed = value.trim();
  if (!QUANTITY_PATTERN.test(trimmed)) throw new Error('Invalid quantity: up to 4 decimal places');
  return { value: trimmed, unit: '' };
}

export function compareQuantities(a: Quantity | string, b: Quantity | string): -1 | 0 | 1 {
  const left = toQuantity(a).value;
  const right = toQuantity(b).value;
  const [leftWhole = '0', leftFraction = ''] = left.split('.');
  const [rightWhole = '0', rightFraction = ''] = right.split('.');
  const whole = BigInt(leftWhole) - BigInt(rightWhole);
  if (whole !== 0n) return whole < 0n ? -1 : 1;
  const scale = Math.max(leftFraction.length, rightFraction.length);
  const fraction = BigInt(leftFraction.padEnd(scale, '0')) - BigInt(rightFraction.padEnd(scale, '0'));
  return fraction < 0n ? -1 : fraction > 0n ? 1 : 0;
}

export function addQuantities(a: Quantity | string, b: Quantity | string): Quantity {
  const left = toQuantity(a);
  const right = toQuantity(b);
  if (left.unit !== right.unit) throw new Error('Unit mismatch');
  const scale = 4;
  const sum = toScaled(left.value, scale) + toScaled(right.value, scale);
  const whole = sum / 10n ** BigInt(scale);
  const fraction = (sum % 10n ** BigInt(scale)).toString().padStart(scale, '0').replace(/0+$/, '');
  return { value: fraction === '' ? `${whole}` : `${whole}.${fraction}`, unit: left.unit };
}

function toQuantity(value: Quantity | string): Quantity {
  return typeof value === 'string' ? parseQuantity(value) : value;
}

function toScaled(value: string, scale: number): bigint {
  const [whole = '0', fraction = ''] = value.split('.');
  return BigInt(whole + fraction.padEnd(scale, '0').slice(0, scale));
}

export type Page<T> = { items: T[]; nextCursor: string | null; total?: number };

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

export type Quantity = { value: string; unit: string };
export type Page<T> = { items: T[]; nextCursor: string | null; total?: number };

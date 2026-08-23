import type { ISODateTime, UUID } from '@pharmacy/core';

export type Currency = 'BDT';
export type Money = { amount: string; currency: Currency };
export type Role = 'owner' | 'manager' | 'cashier' | 'inventory_staff';
export type EntityStatus = 'active' | 'inactive' | 'suspended';

/**
 * The tenders the platform accepts, mirroring `PaymentMethod` in
 * `backend/app/domains/payments.py`.
 *
 * Here rather than in one feature package because two of them disagreed:
 * `@pharmacy/reports` also listed `card`, which no backend enum, migration or API
 * schema has ever accepted. A dashboard could therefore compile a card row that
 * cannot exist, while a card payment posted through the API would be rejected --
 * the breakdown silently defining its own idea of what a shop can take.
 */
export type PaymentMethod = 'cash' | 'bkash' | 'nagad' | 'due';

export type Organization = {
  id: UUID; name: string; slug: string; status: EntityStatus; createdAt: ISODateTime;
};
export type Store = {
  id: UUID; organizationId: UUID; name: string; code: string; timezone: string;
  currency: Currency; status: EntityStatus; createdAt: ISODateTime;
};
export type User = { id: UUID; phone: string; displayName: string; status: EntityStatus; createdAt: ISODateTime };
export type Membership = { userId: UUID; organizationId: UUID; role: Role; status: 'active' | 'inactive' };
export type StoreMembership = { userId: UUID; storeId: UUID; role: Role; status: 'active' | 'inactive' };

export type Session = { accessToken: string; refreshToken: string; expiresAt: ISODateTime; user: User };
export type ApiError = { code: string; message: string; requestId: string; fieldErrors?: Record<string, string[]> };
export type ApiResponse<T> = { data: T; requestId: string };
export type Pagination = { cursor?: string; limit?: number };

export type SyncEnvelope = {
  eventId: UUID; deviceId: UUID; organizationId: UUID; storeId: UUID; userId: UUID;
  eventType: string; createdAt: ISODateTime; clientSequence: number; payload: unknown;
};

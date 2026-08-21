import type { ISODateTime, UUID } from '@pharmacy/core';

export type Currency = 'BDT';
export type Money = { amount: string; currency: Currency };
export type Role = 'owner' | 'manager' | 'cashier' | 'inventory_staff';
export type EntityStatus = 'active' | 'inactive' | 'suspended';

export type Organization = {
  id: UUID; name: string; slug: string; status: EntityStatus; createdAt: ISODateTime;
};
export type Store = {
  id: UUID; organizationId: UUID; name: string; code: string; timezone: string;
  currency: Currency; status: EntityStatus; createdAt: ISODateTime;
};
export type User = { id: UUID; phone: string; displayName: string; status: EntityStatus; createdAt: ISODateTime };
export type Membership = { userId: UUID; organizationId: UUID; role: Role; status: 'active' | 'invited' };
export type StoreMembership = { userId: UUID; storeId: UUID; role: Role; status: 'active' | 'inactive' };

export type Session = { accessToken: string; refreshToken: string; expiresAt: ISODateTime; user: User };
export type ApiError = { code: string; message: string; requestId: string; fieldErrors?: Record<string, string[]> };
export type ApiResponse<T> = { data: T; requestId: string };
export type Pagination = { cursor?: string; limit?: number };

export type SyncEnvelope = {
  eventId: UUID; deviceId: UUID; organizationId: UUID; storeId: UUID; userId: UUID;
  eventType: string; createdAt: ISODateTime; clientSequence: number; payload: unknown;
};

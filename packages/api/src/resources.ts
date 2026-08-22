import type { ApiResponse, Currency, EntityStatus, Membership, Organization, Pagination, Role, Store, StoreMembership, User } from '@pharmacy/types';

import type { ApiClient, RequestOptions } from './client';
import type { Page } from './pagination';

function segment(value: string): string {
  return encodeURIComponent(value);
}

/**
 * Generic CRUD surface over a collection endpoint.
 *
 * `create` and `update` go through the client's mutation helpers, so they carry
 * an idempotency key without every feature package remembering to add one.
 */
export type ResourceClient<TResource, TCreate, TUpdate> = {
  list(pagination?: Pagination, options?: RequestOptions): Promise<Page<TResource>>;
  read(id: string, options?: RequestOptions): Promise<ApiResponse<TResource>>;
  create(body: TCreate, options?: RequestOptions): Promise<ApiResponse<TResource>>;
  update(id: string, body: TUpdate, options?: RequestOptions): Promise<ApiResponse<TResource>>;
  remove(id: string, options?: RequestOptions): Promise<ApiResponse<TResource>>;
};

export function createResourceClient<TResource, TCreate = Partial<TResource>, TUpdate = Partial<TResource>>(
  client: ApiClient,
  basePath: string,
): ResourceClient<TResource, TCreate, TUpdate> {
  const root = basePath.replace(/\/+$/, '');
  return {
    list: (pagination = {}, options = {}) => client.list<TResource>(root, pagination, options),
    read: (id, options = {}) => client.get<TResource>(`${root}/${segment(id)}`, options),
    create: (body, options = {}) => client.post<TResource>(root, body, options),
    update: (id, body, options = {}) => client.patch<TResource>(`${root}/${segment(id)}`, body, options),
    remove: (id, options = {}) => client.delete<TResource>(`${root}/${segment(id)}`, options),
  };
}

/** Mirrors `OrganizationSettings` in `backend/app/schemas/organizations.py`. */
export type OrganizationSettings = {
  defaultTimezone: string;
  defaultCurrency: string;
  locale: string;
  requirePinForDiscounts: boolean;
  expiryAlertDays: number;
  lowStockThresholdDays: number;
  allowNegativeStock: boolean;
  receiptFooter: string | null;
};

export type OrganizationSettingsUpdate = Partial<OrganizationSettings>;

export type OrganizationCreateRequest = { name: string; slug?: string; settings?: OrganizationSettingsUpdate };
export type OrganizationUpdateRequest = { name?: string; slug?: string };
export type OrganizationProfile = Organization & { settings: OrganizationSettings };
export type OrganizationCreateResponse = { organization: OrganizationProfile; role: Role; userId: string };
export type OrganizationSettingsResponse = { organizationId: string; settings: OrganizationSettings };

/** Mirrors `CurrentOrganizationResponse`: the validated tenant context. */
export type CurrentOrganization = {
  organization: Organization;
  role: Role;
  userId: string;
  storeId?: string | null;
  store?: Store | null;
  settings: OrganizationSettings;
};

/** Mirrors `StoreSettings` in `backend/app/schemas/stores.py`. */
export type StoreSettings = {
  receiptHeader: string | null;
  receiptFooter: string | null;
  businessDayCutoffHour: number;
  lowStockAlerts: boolean;
  allowOfflineSales: boolean;
  printReceiptByDefault: boolean;
};

export type StoreSettingsUpdate = Partial<StoreSettings>;
export type StoreCreateRequest = { name: string; code?: string; timezone?: string; currency?: Currency | string; settings?: StoreSettingsUpdate };
export type StoreUpdateRequest = { name?: string; timezone?: string; currency?: Currency | string };
export type StoreProfile = Store & { settings: StoreSettings };
export type StoreSettingsResponse = { storeId: string; settings: StoreSettings };
export type StoreStatusUpdateRequest = { status: EntityStatus; reason?: string | null };

/** Mirrors `StoreOperatingStatusResponse`, including the store-local clock. */
export type StoreOperatingStatus = {
  storeId: string;
  status: EntityStatus;
  operational: boolean;
  timezone: string;
  localTime: string;
  businessDate: string;
};

/** Roles a staff account may be created with; `owner` is bootstrap-only. */
export type StaffRole = Exclude<Role, 'owner'>;

export type UserCreateRequest = { phone: string; displayName: string; role: StaffRole; pin?: string; storeId?: string };
export type UserUpdateRequest = { displayName?: string; phone?: string };
export type UserRoleUpdateRequest = { role: Role; reason?: string | null };
export type UserStatusUpdateRequest = { status: EntityStatus; reason?: string | null };
export type PinSetRequest = { pin: string };
export type StoreAssignmentRequest = { storeId: string };

/** Mirrors `UserProfileResponse`. Never carries a PIN or its hash. */
export type UserProfile = User & {
  membership: Membership;
  storeMemberships: readonly StoreMembership[];
  pinSet: boolean;
};

export type PinStatus = { userId: string; pinSet: boolean };

export type UserListFilters = { role?: Role; status?: EntityStatus; storeId?: string };

/** Typed client for `/organizations` (`backend/app/routers/organizations.py`). */
export type OrganizationsClient = {
  create(body: OrganizationCreateRequest, options?: RequestOptions): Promise<ApiResponse<OrganizationCreateResponse>>;
  current(options?: RequestOptions): Promise<ApiResponse<CurrentOrganization>>;
  readProfile(options?: RequestOptions): Promise<ApiResponse<OrganizationProfile>>;
  updateProfile(body: OrganizationUpdateRequest, options?: RequestOptions): Promise<ApiResponse<OrganizationProfile>>;
  readSettings(options?: RequestOptions): Promise<ApiResponse<OrganizationSettingsResponse>>;
  updateSettings(body: OrganizationSettingsUpdate, options?: RequestOptions): Promise<ApiResponse<OrganizationSettingsResponse>>;
};

export function createOrganizationsClient(client: ApiClient): OrganizationsClient {
  return {
    create: (body, options = {}) => client.post<OrganizationCreateResponse>('/organizations', body, options),
    current: (options = {}) => client.get<CurrentOrganization>('/organizations/current', options),
    readProfile: (options = {}) => client.get<OrganizationProfile>('/organizations/current/profile', options),
    updateProfile: (body, options = {}) => client.patch<OrganizationProfile>('/organizations/current/profile', body, options),
    readSettings: (options = {}) => client.get<OrganizationSettingsResponse>('/organizations/current/settings', options),
    updateSettings: (body, options = {}) => client.patch<OrganizationSettingsResponse>('/organizations/current/settings', body, options),
  };
}

/** Typed client for `/stores` (`backend/app/routers/stores.py`). */
export type StoresClient = {
  list(pagination?: Pagination, options?: RequestOptions): Promise<Page<Store>>;
  create(body: StoreCreateRequest, options?: RequestOptions): Promise<ApiResponse<StoreProfile>>;
  readCurrent(options?: RequestOptions): Promise<ApiResponse<StoreProfile>>;
  readCurrentOperatingStatus(options?: RequestOptions): Promise<ApiResponse<StoreOperatingStatus>>;
  read(storeId: string, options?: RequestOptions): Promise<ApiResponse<StoreProfile>>;
  update(storeId: string, body: StoreUpdateRequest, options?: RequestOptions): Promise<ApiResponse<StoreProfile>>;
  readSettings(storeId: string, options?: RequestOptions): Promise<ApiResponse<StoreSettingsResponse>>;
  updateSettings(storeId: string, body: StoreSettingsUpdate, options?: RequestOptions): Promise<ApiResponse<StoreSettingsResponse>>;
  readOperatingStatus(storeId: string, options?: RequestOptions): Promise<ApiResponse<StoreOperatingStatus>>;
  updateOperatingStatus(storeId: string, body: StoreStatusUpdateRequest, options?: RequestOptions): Promise<ApiResponse<StoreOperatingStatus>>;
};

export function createStoresClient(client: ApiClient): StoresClient {
  return {
    list: (pagination = {}, options = {}) => client.list<Store>('/stores', pagination, options),
    create: (body, options = {}) => client.post<StoreProfile>('/stores', body, options),
    readCurrent: (options = {}) => client.get<StoreProfile>('/stores/current', options),
    readCurrentOperatingStatus: (options = {}) => client.get<StoreOperatingStatus>('/stores/current/operating-status', options),
    read: (storeId, options = {}) => client.get<StoreProfile>(`/stores/${segment(storeId)}`, options),
    update: (storeId, body, options = {}) => client.patch<StoreProfile>(`/stores/${segment(storeId)}`, body, options),
    readSettings: (storeId, options = {}) => client.get<StoreSettingsResponse>(`/stores/${segment(storeId)}/settings`, options),
    updateSettings: (storeId, body, options = {}) => client.patch<StoreSettingsResponse>(`/stores/${segment(storeId)}/settings`, body, options),
    readOperatingStatus: (storeId, options = {}) => client.get<StoreOperatingStatus>(`/stores/${segment(storeId)}/operating-status`, options),
    updateOperatingStatus: (storeId, body, options = {}) =>
      client.patch<StoreOperatingStatus>(`/stores/${segment(storeId)}/operating-status`, body, options),
  };
}

/** Typed client for `/users` (`backend/app/routers/users.py`). */
export type UsersClient = {
  list(filters?: UserListFilters, pagination?: Pagination, options?: RequestOptions): Promise<Page<UserProfile>>;
  create(body: UserCreateRequest, options?: RequestOptions): Promise<ApiResponse<UserProfile>>;
  read(userId: string, options?: RequestOptions): Promise<ApiResponse<UserProfile>>;
  update(userId: string, body: UserUpdateRequest, options?: RequestOptions): Promise<ApiResponse<UserProfile>>;
  changeRole(userId: string, body: UserRoleUpdateRequest, options?: RequestOptions): Promise<ApiResponse<UserProfile>>;
  changeStatus(userId: string, body: UserStatusUpdateRequest, options?: RequestOptions): Promise<ApiResponse<UserProfile>>;
  removeMembership(userId: string, options?: RequestOptions): Promise<ApiResponse<UserProfile>>;
  setPin(userId: string, body: PinSetRequest, options?: RequestOptions): Promise<ApiResponse<PinStatus>>;
  clearPin(userId: string, options?: RequestOptions): Promise<ApiResponse<PinStatus>>;
  assignStore(userId: string, body: StoreAssignmentRequest, options?: RequestOptions): Promise<ApiResponse<UserProfile>>;
  unassignStore(userId: string, storeId: string, options?: RequestOptions): Promise<ApiResponse<UserProfile>>;
};

export function createUsersClient(client: ApiClient): UsersClient {
  return {
    list: (filters = {}, pagination = {}, options = {}) =>
      client.list<UserProfile>('/users', pagination, {
        ...options,
        query: { ...options.query, role: filters.role, status: filters.status, storeId: filters.storeId },
      }),
    create: (body, options = {}) => client.post<UserProfile>('/users', body, options),
    read: (userId, options = {}) => client.get<UserProfile>(`/users/${segment(userId)}`, options),
    update: (userId, body, options = {}) => client.patch<UserProfile>(`/users/${segment(userId)}`, body, options),
    changeRole: (userId, body, options = {}) => client.patch<UserProfile>(`/users/${segment(userId)}/role`, body, options),
    changeStatus: (userId, body, options = {}) => client.patch<UserProfile>(`/users/${segment(userId)}/status`, body, options),
    removeMembership: (userId, options = {}) => client.delete<UserProfile>(`/users/${segment(userId)}/membership`, options),
    setPin: (userId, body, options = {}) => client.put<PinStatus>(`/users/${segment(userId)}/pin`, body, options),
    clearPin: (userId, options = {}) => client.delete<PinStatus>(`/users/${segment(userId)}/pin`, options),
    assignStore: (userId, body, options = {}) => client.post<UserProfile>(`/users/${segment(userId)}/stores`, body, options),
    unassignStore: (userId, storeId, options = {}) =>
      client.delete<UserProfile>(`/users/${segment(userId)}/stores/${segment(storeId)}`, options),
  };
}

/** Every typed client, built over one transport. */
export type PharmacyApi = {
  client: ApiClient;
  organizations: OrganizationsClient;
  stores: StoresClient;
  users: UsersClient;
};

export function createPharmacyApi(client: ApiClient): PharmacyApi {
  return {
    client,
    organizations: createOrganizationsClient(client),
    stores: createStoresClient(client),
    users: createUsersClient(client),
  };
}

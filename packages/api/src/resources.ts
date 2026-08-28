import type { ApiResponse, Currency, EntityStatus, Membership, Organization, Pagination, PaymentMethod, Role, Store, StoreMembership, User } from '@pharmacy/types';

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
  receiptLogo: string | null;
  receiptBusinessName: string | null;
  receiptAddress: string | null;
  receiptPhone: string | null;
  receiptEmail: string | null;
  receiptTaxId: string | null;
  receiptPaperWidthMm: number;
  receiptShowLogo: boolean;
  receiptShowBusinessName: boolean;
  receiptShowStoreName: boolean;
  receiptShowContactDetails: boolean;
  receiptShowHeader: boolean;
  receiptShowReceiptNumber: boolean;
  receiptShowDateTime: boolean;
  receiptShowCustomer: boolean;
  receiptShowCashier: boolean;
  receiptShowItems: boolean;
  receiptShowItemQuantity: boolean;
  receiptShowUnitPrice: boolean;
  receiptShowLineTotal: boolean;
  receiptShowSubtotal: boolean;
  receiptShowDiscounts: boolean;
  receiptShowCharges: boolean;
  receiptShowTotal: boolean;
  receiptShowPayments: boolean;
  receiptShowCashReceived: boolean;
  receiptShowChangeDue: boolean;
  receiptShowFooter: boolean;
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

/** Mirrors `DeviceClaim` in `backend/app/schemas/auth.py`. */
export type DeviceClaim = { deviceKey: string; deviceName: string };

export type OtpPurpose = 'login' | 'signup' | 'phone_change';
export type OtpRequest = { phone: string; purpose?: OtpPurpose };
export type OtpChallenge = { challengeId: string; expiresAt: string; devCode?: string | null };
export type OtpVerifyRequest = { challengeId: string; code: string; displayName?: string; device?: DeviceClaim };
export type PinLoginRequest = { phone: string; pin: string; organizationId: string; storeId?: string; device?: DeviceClaim };
export type RefreshRequest = { refreshToken: string };
export type SelectContextRequest = { organizationId: string; storeId?: string; device?: DeviceClaim };

/** Mirrors `MembershipStore`: a branch named well enough to put on a button. */
export type MembershipStore = { id: string; code: string; name: string };

export type MembershipOption = { organizationId: string; organizationName: string; role: Role; stores: readonly MembershipStore[] };

/** Mirrors `TokenResponse`: the credential pair plus the context-selection shell. */
export type TokenBundle = {
  accessToken: string;
  refreshToken: string;
  tokenType: 'bearer';
  expiresIn: number;
  sessionId: string;
  user: User;
  organizationId?: string | null;
  storeId?: string | null;
  role?: Role | null;
  requiresOrganization: boolean;
  organizations: readonly MembershipOption[];
};

export type AuthSession = {
  id: string;
  organizationId?: string | null;
  storeId?: string | null;
  deviceId?: string | null;
  expiresAt: string;
  createdAt: string;
  revokedAt?: string | null;
  current: boolean;
};

/** Mirrors `CurrentUserResponse` (`GET /auth/me`): live rows, not token claims. */
export type CurrentUser = {
  user: User;
  organizationId: string;
  organizationName: string;
  role: Role;
  storeId?: string | null;
  storeName?: string | null;
  deviceId?: string | null;
  pinSet: boolean;
  sessionId: string;
  sessionExpiresAt: string;
};

export type DeviceStatus = 'active' | 'revoked';
export type DeviceRegisterRequest = { deviceKey: string; name: string };
export type Device = {
  id: string;
  organizationId: string;
  storeId: string;
  deviceKey: string;
  name: string;
  status: DeviceStatus;
  lastSeenAt?: string | null;
  createdAt: string;
};

export type LogoutResult = { revokedSessionIds: readonly string[] };

export type SessionListFilters = { userId?: string };

/**
 * Typed client for `/auth` (`backend/app/routers/auth.py`).
 *
 * Three wire traps are encoded here on purpose: `listSessions` sends `user_id`
 * in snake_case (the backend reads the raw query name and silently ignores a
 * camelCased one), `DeviceRegisterRequest.name` is `deviceName` on the login
 * claim, and every body is `extra="forbid"` server-side, so callers must pass
 * exactly the typed shape -- never spread extra client state into a body.
 */
export type AuthClient = {
  requestOtp(body: OtpRequest, options?: RequestOptions): Promise<ApiResponse<OtpChallenge>>;
  verifyOtp(body: OtpVerifyRequest, options?: RequestOptions): Promise<ApiResponse<TokenBundle>>;
  loginWithPin(body: PinLoginRequest, options?: RequestOptions): Promise<ApiResponse<TokenBundle>>;
  refresh(body: RefreshRequest, options?: RequestOptions): Promise<ApiResponse<TokenBundle>>;
  selectContext(body: SelectContextRequest, options?: RequestOptions): Promise<ApiResponse<TokenBundle>>;
  me(options?: RequestOptions): Promise<ApiResponse<CurrentUser>>;
  listSessions(filters?: SessionListFilters, options?: RequestOptions): Promise<ApiResponse<readonly AuthSession[]>>;
  logout(options?: RequestOptions): Promise<ApiResponse<LogoutResult>>;
  revokeSession(sessionId: string, options?: RequestOptions): Promise<ApiResponse<AuthSession>>;
  registerDevice(body: DeviceRegisterRequest, options?: RequestOptions): Promise<ApiResponse<Device>>;
  listDevices(options?: RequestOptions): Promise<ApiResponse<readonly Device[]>>;
  revokeDevice(deviceId: string, options?: RequestOptions): Promise<ApiResponse<Device>>;
};

const ANONYMOUS: RequestOptions = { anonymous: true };

export function createAuthClient(client: ApiClient): AuthClient {
  return {
    requestOtp: (body, options = {}) => client.post<OtpChallenge>('/auth/otp/request', body, { ...ANONYMOUS, ...options }),
    verifyOtp: (body, options = {}) => client.post<TokenBundle>('/auth/otp/verify', body, { ...ANONYMOUS, ...options }),
    loginWithPin: (body, options = {}) => client.post<TokenBundle>('/auth/pin/login', body, { ...ANONYMOUS, ...options }),
    refresh: (body, options = {}) => client.post<TokenBundle>('/auth/refresh', body, { ...ANONYMOUS, ...options }),
    // Bearer-authenticated but valid before an organization is chosen: this is
    // the post-OTP tenant-selection hop, so the token may carry no `org` claim.
    selectContext: (body, options = {}) => client.post<TokenBundle>('/auth/context', body, options),
    me: (options = {}) => client.get<CurrentUser>('/auth/me', options),
    listSessions: (filters = {}, options = {}) =>
      client.request<readonly AuthSession[]>('/auth/sessions', { method: 'GET' }, {
        ...options,
        query: { ...options.query, user_id: filters.userId },
      }),
    logout: (options = {}) => client.post<LogoutResult>('/auth/logout', undefined, options),
    revokeSession: (sessionId, options = {}) =>
      client.post<AuthSession>(`/auth/sessions/${segment(sessionId)}/revoke`, undefined, options),
    // Returns 201; owner/manager only (the router's AdminDep).
    registerDevice: (body, options = {}) => client.post<Device>('/auth/devices', body, options),
    listDevices: (options = {}) => client.get<readonly Device[]>('/auth/devices', options),
    revokeDevice: (deviceId, options = {}) =>
      client.post<Device>(`/auth/devices/${segment(deviceId)}/revoke`, undefined, options),
  };
}

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
  auth: AuthClient;
  organizations: OrganizationsClient;
  stores: StoresClient;
  users: UsersClient;
  products: ProductsClient;
  catalog: CatalogClient;
  purchaseOrders: PurchaseOrdersClient;
  inventory: InventoryClient;
  purchases: PurchasesClient;
  suppliers: SuppliersClient;
  sales: SalesClient;
  payments: PaymentsClient;
  customers: CustomersClient;
  reports: ReportsClient;
  sync: SyncClient;
  orders: OrdersClient;
  ecommerce: EcommerceClient;
  storefront: StorefrontClient;
  prescriptions: PrescriptionsClient;
};

export function createPharmacyApi(client: ApiClient): PharmacyApi {
  return {
    client,
    auth: createAuthClient(client),
    organizations: createOrganizationsClient(client),
    stores: createStoresClient(client),
    users: createUsersClient(client),
    products: createProductsClient(client),
    catalog: createCatalogClient(client),
    purchaseOrders: createPurchaseOrdersClient(client),
    inventory: createInventoryClient(client),
    purchases: createPurchasesClient(client),
    suppliers: createSuppliersClient(client),
    sales: createSalesClient(client),
    payments: createPaymentsClient(client),
    customers: createCustomersClient(client),
    reports: createReportsClient(client),
    sync: createSyncClient(client),
    orders: createOrdersClient(client),
    ecommerce: createEcommerceClient(client),
    storefront: createStorefrontClient(client),
    prescriptions: createPrescriptionsClient(client),
  };
}

// --- POS phase: products, sales, payments, customers, reports, sync -------------

export type StoreProduct = {
  id: string;
  organizationId: string;
  storeId: string;
  pharmacyProductId: string;
  sku: string;
  /** Decimal serialized as a fixed-cents string, e.g. `"10.00"`. */
  salePrice: string;
  minimumStock: string;
  rack?: string | null;
  active: boolean;
  createdAt: string;
};

/**
 * Mirrors `ShelfItemResponse`: a shelf row with the product it sells folded in.
 *
 * What `GET /products/current` answers. The extra two fields are the ones a
 * counter cannot work without -- `name`, because a cashier picking from a list of
 * bare SKUs is picking from memory, and `barcode`, because a scan has to resolve
 * on the device for the cached shelf to be worth caching.
 */
export type ShelfItem = StoreProduct & {
  name: string;
  unit: string;
  barcode?: string | null;
  genericName?: string | null;
  strength?: string | null;
  manufacturerId?: string | null;
  manufacturer?: string | null;
  dosageFormId?: string | null;
  dosageForm?: string | null;
  availableQuantity: string;
};

export type SaleStatus = 'completed' | 'voided' | 'refunded';
export type SaleChannel = 'pos' | 'online';
/** Re-exported from `@pharmacy/types` so the wire, the reports and the backend enum cannot drift apart. */
export type { PaymentMethod };
export type PaymentStatus = 'pending' | 'captured' | 'failed' | 'refunded';

export type Payment = {
  id: string;
  organizationId: string;
  storeId: string;
  referenceType: string;
  referenceId: string;
  customerId?: string | null;
  method: PaymentMethod;
  amount: string;
  receivedAmount?: string | null;
  status: PaymentStatus;
  providerReference?: string | null;
  createdAt: string;
};

export type SaleItem = {
  id: string;
  storeProductId: string;
  productName: string;
  quantity: string;
  unitPrice: string;
  discountMode?: DiscountMode | null;
  discountValue: string;
  discountAmount: string;
  lineTotal: string;
};

export type DiscountMode = 'percentage' | 'flat';
export type DiscountInput = { mode: DiscountMode; value: string };
export type SaleChargeInput = { kind: 'delivery' | 'other'; amount: string; label?: string };
export type AdvanceApplicationInput = { amount: string; reference?: string };

export type Sale = {
  id: string;
  organizationId: string;
  storeId: string;
  customerId?: string | null;
  channel: SaleChannel;
  status: SaleStatus;
  subtotal: string;
  discount: string;
  lineDiscount: string;
  globalDiscount: string;
  deliveryCharge: string;
  otherFeeLabel?: string | null;
  otherFee: string;
  advanceApplied: string;
  advanceReference?: string | null;
  amountDueNow: string;
  total: string;
  receiptNumber?: string | null;
  voidReason?: string | null;
  createdAt: string;
  items: readonly SaleItem[];
  payments: readonly Payment[];
};

export type SalePaymentInput = {
  method: PaymentMethod;
  amount: string;
  receivedAmount?: string;
  providerReference?: string;
};

export type SaleCreateRequest = {
  customerId?: string | null;
  discount?: string;
  globalDiscount?: DiscountInput;
  charges?: readonly SaleChargeInput[];
  advanceApplication?: AdvanceApplicationInput;
  discountApprovalToken?: string;
  items: readonly { storeProductId: string; quantity: string; discount?: DiscountInput }[];
  payments: readonly SalePaymentInput[];
  /** Client display echo only; the server always recomputes from shelf prices. */
  subtotal?: string;
  total?: string;
};

export type DiscountApprovalRequest = {
  phone: string;
  pin: string;
  items: SaleCreateRequest['items'];
  discount?: string;
  globalDiscount?: DiscountInput;
  charges?: readonly SaleChargeInput[];
};
export type DiscountApproval = { token: string; expiresAt: string; approvedBy: string };

export type SaleReturnRequest = { reason: string; lines: readonly { saleItemId: string; quantity: string }[] };

export type SaleListFilters = { customerId?: string; status?: SaleStatus };

export type SalesClient = {
  list(filters?: SaleListFilters, pagination?: Pagination, options?: RequestOptions): Promise<Page<Sale>>;
  read(saleId: string, options?: RequestOptions): Promise<ApiResponse<Sale>>;
  /** Requires an idempotency key (send one via `options.idempotencyKey`). */
  create(body: SaleCreateRequest, options?: RequestOptions): Promise<ApiResponse<Sale>>;
  approveDiscount(body: DiscountApprovalRequest, options?: RequestOptions): Promise<ApiResponse<DiscountApproval>>;
  /** Requires an idempotency key (send one via `options.idempotencyKey`). */
  createReturn(saleId: string, body: SaleReturnRequest, options?: RequestOptions): Promise<ApiResponse<{ id: string; saleId: string; reason: string; total: string; createdAt: string }>>;
  void(saleId: string, body: { reason: string }, options?: RequestOptions): Promise<ApiResponse<Sale>>;
};

export function createSalesClient(client: ApiClient): SalesClient {
  return {
    list: (filters = {}, pagination = {}, options = {}) =>
      client.list<Sale>('/sales', pagination, {
        ...options,
        query: { ...options.query, customerId: filters.customerId, status: filters.status },
      }),
    read: (saleId, options = {}) => client.get<Sale>(`/sales/${segment(saleId)}`, options),
    create: (body, options = {}) => client.post<Sale>('/sales', body, options),
    approveDiscount: (body, options = {}) => client.post<DiscountApproval>('/sales/discount-approvals', body, options),
    createReturn: (saleId, body, options = {}) =>
      client.post<{ id: string; saleId: string; reason: string; total: string; createdAt: string }>(
        `/sales/${segment(saleId)}/returns`,
        body,
        options,
      ),
    void: (saleId, body, options = {}) =>
      client.post<Sale>(`/sales/${segment(saleId)}/void`, body, options),
  };
}

export type PaymentsClient = {
  list(
    filters?: { referenceType?: string; referenceId?: string; customerId?: string; method?: PaymentMethod; status?: PaymentStatus },
    pagination?: Pagination,
    options?: RequestOptions,
  ): Promise<Page<Payment>>;
  read(paymentId: string, options?: RequestOptions): Promise<ApiResponse<Payment>>;
  updateStatus(paymentId: string, body: { status: PaymentStatus; providerReference?: string }, options?: RequestOptions): Promise<ApiResponse<Payment>>;
};

export function createPaymentsClient(client: ApiClient): PaymentsClient {
  return {
    list: (filters = {}, pagination = {}, options = {}) =>
      client.list<Payment>('/payments', pagination, {
        ...options,
        query: {
          ...options.query,
          referenceType: filters.referenceType,
          referenceId: filters.referenceId,
          customerId: filters.customerId,
          method: filters.method,
          status: filters.status,
        },
      }),
    read: (paymentId, options = {}) => client.get<Payment>(`/payments/${segment(paymentId)}`, options),
    updateStatus: (paymentId, body, options = {}) =>
      client.post<Payment>(`/payments/${segment(paymentId)}/status`, body, options),
  };
}

export type Customer = {
  id: string;
  organizationId: string;
  name: string;
  normalizedPhone?: string | null;
  email?: string | null;
  dueBalance: string;
  advanceBalance: string;
  preferences: Record<string, unknown>;
  active: boolean;
  createdAt: string;
};

/**
 * The field is `normalizedPhone`, not `phone`: the server canonicalises whatever
 * dialing form is sent (`01712345678`, `+8801712345678`) onto `+8801XXXXXXXXX` and
 * stores it under that name. Request models forbid unknown keys, so sending `phone`
 * is a 422 rather than a silently dropped field.
 */
export type CustomerCreateRequest = { name: string; normalizedPhone?: string; email?: string; preferences?: Record<string, unknown> };
export type CustomerUpdateRequest = { name?: string; normalizedPhone?: string; email?: string; preferences?: Record<string, unknown> };
/** `totalSpent` is null when the caller's role may not see lifetime spend; `totalDue` is always populated so a cashier can take a payment against it. */
export type CustomerHistorySummary = { customerId: string; saleCount: number; totalSpent?: string | null; totalRefunded: string; totalDue: string };
export type CustomerAddress = { id: string; customerId: string; label: string; addressLine: string; city?: string | null; postalCode?: string | null; active: boolean; createdAt: string };
export type CustomerAddressCreateRequest = { label: string; addressLine: string; city?: string; postalCode?: string };
/** Omitting `active` means active-only; `hasDue` narrows within that rather than replacing it. */
export type CustomerFilters = { q?: string; active?: boolean; hasDue?: boolean };

export type CustomersClient = {
  search(query: CustomerFilters, pagination?: Pagination, options?: RequestOptions): Promise<Page<Customer>>;
  create(body: CustomerCreateRequest, options?: RequestOptions): Promise<ApiResponse<Customer>>;
  read(customerId: string, options?: RequestOptions): Promise<ApiResponse<Customer>>;
  update(customerId: string, body: CustomerUpdateRequest, options?: RequestOptions): Promise<ApiResponse<Customer>>;
  deactivate(customerId: string, options?: RequestOptions): Promise<ApiResponse<Customer>>;
  history(customerId: string, options?: RequestOptions): Promise<ApiResponse<CustomerHistorySummary>>;
  /** Recompute `dueBalance` from the payment ledger; owner/manager only, and idempotent. */
  rebuildDueBalance(customerId: string, options?: RequestOptions): Promise<ApiResponse<Customer>>;
  /** Delivery addresses. The list is active-only; there is no server-side edit, so correcting one means adding a replacement. */
  listAddresses(customerId: string, options?: RequestOptions): Promise<ApiResponse<CustomerAddress[]>>;
  createAddress(customerId: string, body: CustomerAddressCreateRequest, options?: RequestOptions): Promise<ApiResponse<CustomerAddress>>;
};

export function createCustomersClient(client: ApiClient): CustomersClient {
  return {
    search: ({ q, active, hasDue }, pagination = {}, options = {}) =>
      client.list<Customer>('/customers', pagination, { ...options, query: { ...options.query, q, active, hasDue } }),
    create: (body, options = {}) => client.post<Customer>('/customers', body, options),
    read: (customerId, options = {}) => client.get<Customer>(`/customers/${segment(customerId)}`, options),
    update: (customerId, body, options = {}) => client.patch<Customer>(`/customers/${segment(customerId)}`, body, options),
    deactivate: (customerId, options = {}) => client.delete<Customer>(`/customers/${segment(customerId)}`, options),
    history: (customerId, options = {}) =>
      client.get<CustomerHistorySummary>(`/customers/${segment(customerId)}/history`, options),
    rebuildDueBalance: (customerId, options = {}) =>
      client.post<Customer>(`/customers/${segment(customerId)}/due/rebuild`, {}, options),
    listAddresses: (customerId, options = {}) =>
      client.get<CustomerAddress[]>(`/customers/${segment(customerId)}/addresses`, options),
    createAddress: (customerId, body, options = {}) =>
      client.post<CustomerAddress>(`/customers/${segment(customerId)}/addresses`, body, options),
  };
}

export type TodayMetrics = {
  /** The store-local trading day these figures cover, honouring its cutoff hour. */
  businessDate: string;
  salesTotal: string;
  refundTotal: string;
  netSalesTotal: string;
  transactionCount: number;
  /**
   * Net movement per tender, keyed on the payment and refund timestamps rather than
   * the sale's. A line can be negative: a morning spent refunding last night's big
   * sale really does leave the drawer down, and clamping that at zero is what makes
   * a till stop reconciling.
   */
  paymentBreakdown: Record<string, string>;
  /** Money that actually moved: captured payments less refunds, excluding `due`. */
  collectedTotal: string;
  /** Credit extended today -- owed by customers, not in the drawer. */
  dueTotal: string;
  expenseTotal: string;
  /** Null when the caller's role is not allowed to see cost/profit. */
  profit?: string | null;
  asOf: string;
};

/** A rebuilt `daily_store_metrics` projection, reconciled against the ledgers. */
export type DailyMetric = {
  storeId: string;
  metricDate: string;
  salesTotal: string;
  refundTotal: string;
  costTotal: string;
  paymentBreakdown: Record<string, string>;
  /**
   * Derived from `paymentBreakdown` by the same code path as `TodayMetrics`, so a
   * rebuilt day and a live day cannot disagree about what the drawer should hold.
   */
  collectedTotal: string;
  rebuiltAt: string;
};

/** `productName` falls back to the SKU server-side, so it is never null. */
export type LowStockItem = { storeProductId: string; sku: string; productName: string; available: string; minimumStock: string };
/** `daysUntilExpiry` is counted from the branch's trading day, not UTC, so it does not shift for a store east of Greenwich. */
export type ExpiryWarning = { batchId: string; storeProductId: string; sku: string; productName: string; batchNumber: string; expiryDate: string; available: string; daysUntilExpiry: number };
export type Expense = { id: string; storeId: string; category: string; amount: string; expenseDate: string; note?: string | null; createdByUserId?: string | null; createdAt: string };
export type ExpenseCreateRequest = { category: string; amount: string; expenseDate: string; note?: string };
export type ExpenseFilters = { from?: string; to?: string };

export type ReportsClient = {
  /** `asOf` selects a past trading day, e.g. closing yesterday's books after midnight. */
  today(asOf?: string, options?: RequestOptions): Promise<ApiResponse<TodayMetrics>>;
  rebuildDailyMetric(asOf?: string, options?: RequestOptions): Promise<ApiResponse<DailyMetric>>;
  lowStock(options?: RequestOptions): Promise<ApiResponse<readonly LowStockItem[]>>;
  expiry(withinDays?: number, options?: RequestOptions): Promise<ApiResponse<readonly ExpiryWarning[]>>;
  listExpenses(filters?: ExpenseFilters, pagination?: Pagination, options?: RequestOptions): Promise<Page<Expense>>;
  createExpense(body: ExpenseCreateRequest, options?: RequestOptions): Promise<ApiResponse<Expense>>;
};

export function createReportsClient(client: ApiClient): ReportsClient {
  return {
    today: (asOf, options = {}) =>
      client.get<TodayMetrics>('/reports/today', { ...options, query: { ...options.query, asOf } }),
    rebuildDailyMetric: (asOf, options = {}) =>
      client.post<DailyMetric>('/reports/daily-metrics/rebuild', undefined, {
        ...options,
        query: { ...options.query, asOf },
      }),
    lowStock: (options = {}) => client.get<readonly LowStockItem[]>('/reports/low-stock', options),
    expiry: (withinDays = 30, options = {}) =>
      client.get<readonly ExpiryWarning[]>('/reports/expiry', { ...options, query: { ...options.query, withinDays } }),
    listExpenses: ({ from, to } = {}, pagination = {}, options = {}) =>
      client.list<Expense>('/reports/expenses', pagination, { ...options, query: { ...options.query, from, to } }),
    createExpense: (body, options = {}) => client.post<Expense>('/reports/expenses', body, options),
  };
}

export type SyncDevice = { id: string; storeId: string; name: string; deviceKey: string; status: DeviceStatus; createdAt: string };

/**
 * One offline mutation on the wire, mirroring `SyncEventEnvelopeIn`.
 *
 * The identity fields are optional on the server but should always be sent. They
 * are not trusted -- ingest checks each against the bearer token and answers
 * `IDENTITY_MISMATCH` on disagreement -- which is exactly why they are worth
 * sending: a queue filled up at one store and flushed after signing in at
 * another is refused instead of being quietly booked against the second shop's
 * stock and takings. Omitting them means the token decides, and the token is
 * whatever the device is holding at flush time.
 *
 * Nothing else may be added: the request model forbids unknown keys, so a client
 * that spreads its own richer envelope onto the wire gets the whole batch
 * rejected as malformed. `@pharmacy/sync`'s `idempotencyKey` in particular is a
 * local concern and must be stripped.
 */
export type SyncEnvelope = {
  eventId: string;
  eventType: string;
  clientSequence: number;
  payload: Record<string, unknown>;
  createdAt?: string;
  deviceId?: string;
  organizationId?: string;
  storeId?: string;
  userId?: string;
};

export type SyncAck = { eventId: string; serverSequence?: number | null; duplicate: boolean; errorCode?: string | null };
export type SyncPullChange = { serverSequence: number; eventType: string; payload: Record<string, unknown>; receivedAt: string };

export type SyncClient = {
  registerDevice(body: { name: string; deviceKey: string }, options?: RequestOptions): Promise<ApiResponse<SyncDevice>>;
  listDevices(options?: RequestOptions): Promise<ApiResponse<readonly SyncDevice[]>>;
  revokeDevice(deviceId: string, options?: RequestOptions): Promise<ApiResponse<SyncDevice>>;
  ingest(events: readonly SyncEnvelope[], options?: RequestOptions): Promise<ApiResponse<{ acks: readonly SyncAck[] }>>;
  pull(cursor: number, limit?: number, options?: RequestOptions): Promise<ApiResponse<{ changes: readonly SyncPullChange[]; nextCursor: number; hasMore: boolean }>>;
};

export function createSyncClient(client: ApiClient): SyncClient {
  return {
    registerDevice: (body, options = {}) => client.post<SyncDevice>('/sync/devices', body, options),
    listDevices: (options = {}) => client.get<readonly SyncDevice[]>('/sync/devices', options),
    revokeDevice: (deviceId, options = {}) =>
      client.post<SyncDevice>(`/sync/devices/${segment(deviceId)}/revoke`, undefined, options),
    ingest: (events, options = {}) => client.post<{ acks: readonly SyncAck[] }>('/sync/events', { events }, options),
    pull: (cursor, limit = 50, options = {}) =>
      client.get<{ changes: readonly SyncPullChange[]; nextCursor: number; hasMore: boolean }>('/sync/events', {
        ...options,
        query: { ...options.query, cursor, limit },
      }),
  };
}

// --- Catalogue / stock screens -----------------------------------------------------

export type PharmacyProduct = {
  id: string;
  organizationId: string;
  catalogProductId?: string | null;
  name: string;
  barcode?: string | null;
  unit: string;
  active: boolean;
  createdAt: string;
};

export type PharmacyProductCreateRequest = { name: string; unit: string; catalogProductId?: string; barcode?: string };

export type StoreProductEnableRequest = { pharmacyProductId: string; sku: string; salePrice: string; minimumStock?: string; rack?: string };

export type StockRow = { storeProductId: string; onHand: string; reserved: string; available: string; lowStock: boolean };
export type ExpiringBatch = { batchId: string; storeProductId: string; batchNumber: string; expiryDate?: string | null; available: string; daysUntilExpiry: number; expired: boolean };
export type ReceiveBatchRequest = { storeProductId: string; batchNumber: string; expiryDate?: string; unitCost: string; quantity: string };
export type InventoryIntakeRequest = {
  source: 'opening_stock' | 'supplier_receive';
  storeProductId?: string;
  pharmacyProductId?: string;
  catalogProductId?: string;
  customProduct?: { name: string; unit: string; barcode?: string };
  shelf?: { salePrice?: string; sku?: string; barcode?: string; rack?: string; minimumStock?: string };
  quantity: string;
  unitCost?: string;
  batchNumber?: string;
  expiryDate?: string;
  supplierId?: string;
  reference?: string;
};
export type InventoryIntake = {
  storeProductId: string;
  pharmacyProductId: string;
  name: string;
  sku: string;
  barcode?: string | null;
  salePrice: string;
  rack?: string | null;
  unit: string;
  adopted: boolean;
  batch: { id: string; batchNumber: string; expiryDate?: string | null; unitCost: string; receivedAt: string };
  movement: { id: string; storeProductId: string; batchId?: string | null; movementType: string; quantity: string; occurredAt: string };
  balance: { storeProductId: string; onHand: string; reserved: string; available: string };
};

export type ProductsClient = {
  listPharmacyProducts(pagination?: Pagination, options?: RequestOptions): Promise<Page<PharmacyProduct>>;
  createPharmacyProduct(body: PharmacyProductCreateRequest, options?: RequestOptions): Promise<ApiResponse<PharmacyProduct>>;
  /** Shelf list for the branch the token is pinned to, product names and barcodes included. */
  listCurrentStoreProducts(options?: RequestOptions & { includeInactive?: boolean }): Promise<Page<ShelfItem>>;
  enableStoreProduct(body: StoreProductEnableRequest, options?: RequestOptions): Promise<ApiResponse<StoreProduct>>;
  /** Unified catalogue + shop search; every store role may call it. */
  search(params: CatalogSearchParams, pagination?: Pagination, options?: RequestOptions): Promise<Page<CatalogSearchItem>>;
  /** Other brands of one generic, this shop's status on each; every store role may call it. */
  alternatives(params: CatalogAlternativesParams, pagination?: Pagination, options?: RequestOptions): Promise<Page<CatalogAlternativeItem>>;
  /** Owner/manager only: adopt a catalogue entry into the shop and onto a shelf. */
  adopt(body: AdoptPayload, options?: RequestOptions): Promise<ApiResponse<AdoptResult>>;
};

export function createProductsClient(client: ApiClient): ProductsClient {
  return {
    listPharmacyProducts: (pagination = {}, options = {}) => client.list<PharmacyProduct>('/products', pagination, options),
    createPharmacyProduct: (body, options = {}) => client.post<PharmacyProduct>('/products', body, options),
    listCurrentStoreProducts: ({ includeInactive = false, ...options } = {}) =>
      client.list<ShelfItem>('/products/current', {}, { ...options, query: { ...options.query, includeInactive } }),
    enableStoreProduct: (body, options = {}) => client.post<StoreProduct>('/products/current', body, options),
    search: ({ q }, pagination = {}, options = {}) =>
      client.list<CatalogSearchItem>('/products/search', pagination, { ...options, query: { ...options.query, q } }),
    alternatives: ({ genericName, excludeCatalogProductId, strength, dosageFormId }, pagination = {}, options = {}) =>
      client.list<CatalogAlternativeItem>('/products/alternatives', pagination, {
        ...options,
        query: { ...options.query, genericName, excludeCatalogProductId, strength, dosageFormId },
      }),
    adopt: (body, options = {}) => client.post<AdoptResult>('/products/adopt', body, options),
  };
}

// --- Catalogue adoption + purchase orders ------------------------------------------

/** Mirrors `CatalogSearchItemResponse`: one merged row of `GET /products/search`. */
export type CatalogSearchItem = {
  kind: 'catalog' | 'custom';
  catalogProductId?: string | null;
  pharmacyProductId?: string | null;
  storeProductId?: string | null;
  /** `on_shelf` needs an active shelf row at the pinned store, then `in_org`, then `absent`. */
  shopStatus: 'on_shelf' | 'in_org' | 'absent';
  name: string;
  barcode?: string | null;
  genericName?: string | null;
  strength?: string | null;
  dosageFormId?: string | null;
  dosageForm?: string | null;
  manufacturerId?: string | null;
  manufacturer?: string | null;
  packageSize?: string | null;
  packageUnit?: string | null;
  prescriptionRequired: boolean;
  referenceUnitPrice?: string | null;
  referenceStripPrice?: string | null;
  salePrice?: string | null;
  availableQuantity?: string | null;
  sku?: string | null;
  matchedField: 'barcode' | 'sku' | 'name' | 'genericName' | 'alias' | 'strength' | 'dosageForm';
  matchQuality: 'exact' | 'partial' | 'fuzzy' | 'supporting';
  matchedText: string;
  matchScore: number;
};

export type CatalogSearchParams = { q: string };

/** Mirrors `CatalogAlternativeItemResponse`: one row of `GET /products/alternatives`. */
export type CatalogAlternativeItem = {
  catalogProductId: string;
  pharmacyProductId?: string | null;
  storeProductId?: string | null;
  shopStatus: 'on_shelf' | 'in_org' | 'absent';
  name: string;
  genericName?: string | null;
  strength?: string | null;
  dosageFormId?: string | null;
  dosageForm?: string | null;
  manufacturerId?: string | null;
  manufacturer?: string | null;
  packageSize?: string | null;
  packageUnit?: string | null;
  prescriptionRequired: boolean;
  referenceUnitPrice?: string | null;
  referenceStripPrice?: string | null;
  salePrice?: string | null;
  availableQuantity?: string | null;
  sku?: string | null;
  /** Relative to the row that was asked about: strength/form equality, never a filter. */
  sameStrength: boolean;
  sameDosageForm: boolean;
};

export type CatalogAlternativesParams = {
  genericName: string;
  /** Drops the catalogue row the caller is already looking at. */
  excludeCatalogProductId?: string;
  strength?: string;
  dosageFormId?: string;
};

export type AdoptPayload = {
  catalogProductId: string;
  storeId?: string;
  sku?: string;
  salePrice?: string;
  minimumStock?: string;
  rack?: string;
};

export type AdoptResult = { pharmacyProduct: PharmacyProduct; storeProduct: StoreProduct };

/** Mirrors `CatalogProductResponse` for the owner's manual entry form. */
export type CatalogProductCreateRequest = {
  name: string;
  genericName?: string;
  manufacturerId?: string;
  dosageFormId?: string;
  strength?: string;
  packageSize?: string;
  packageUnit: string;
  prescriptionRequired?: boolean;
  countryCode: string;
  active?: boolean;
  unitPrice?: string;
  stripPrice?: string;
};

export type CatalogReference = { id: string; name: string; countryCode?: string | null; active: boolean; createdAt: string };

export type CatalogClient = {
  listManufacturers(options?: RequestOptions): Promise<Page<CatalogReference>>;
  listDosageForms(options?: RequestOptions): Promise<Page<CatalogReference>>;
  createProduct(body: CatalogProductCreateRequest, options?: RequestOptions): Promise<ApiResponse<CatalogProductRecord>>;
};

/** Mirrors `CatalogProductResponse` (children lists omitted from the row shape). */
export type CatalogProductRecord = {
  id: string;
  name: string;
  genericName?: string | null;
  manufacturerId?: string | null;
  dosageFormId?: string | null;
  strength?: string | null;
  packageSize: string;
  packageUnit: string;
  prescriptionRequired: boolean;
  countryCode: string;
  active: boolean;
  unitPrice?: string | null;
  stripPrice?: string | null;
  createdAt: string;
};

export type PurchaseOrderStatusWire = 'draft' | 'ordered' | 'closed' | 'cancelled';

/** Owner/manager reference data behind the manual catalogue-entry form. */
export function createCatalogClient(client: ApiClient): CatalogClient {
  return {
    listManufacturers: (options = {}) => client.list<CatalogReference>('/catalog/manufacturers', {}, options),
    listDosageForms: (options = {}) => client.list<CatalogReference>('/catalog/dosage-forms', {}, options),
    createProduct: (body, options = {}) => client.post<CatalogProductRecord>('/catalog/products', body, options),
  };
}

/** Mirrors `PurchaseOrderItemResponse`. */
export type PurchaseOrderItem = {
  id: string;
  purchaseOrderId: string;
  catalogProductId?: string | null;
  pharmacyProductId?: string | null;
  name: string;
  quantity: string;
  estUnitCost?: string | null;
};

/** Mirrors `PurchaseOrderResponse`. */
export type PurchaseOrder = {
  id: string;
  organizationId: string;
  storeId: string;
  supplierId?: string | null;
  status: PurchaseOrderStatusWire;
  expectedAt?: string | null;
  note?: string | null;
  orderedAt?: string | null;
  closedAt?: string | null;
  cancelledAt?: string | null;
  createdAt: string;
  items: readonly PurchaseOrderItem[];
};

export type PurchaseOrderCreateRequest = {
  supplierId?: string;
  expectedAt?: string;
  note?: string;
  items?: readonly { name: string; quantity: string; estUnitCost?: string; catalogProductId?: string; pharmacyProductId?: string }[];
};

export type PurchaseOrderItemAddRequest = {
  name: string;
  quantity: string;
  estUnitCost?: string;
  catalogProductId?: string;
  pharmacyProductId?: string;
};

export type PurchaseOrderItemUpdateRequest = { name?: string; quantity?: string; estUnitCost?: string };

export type PoConvertResult = {
  purchaseId: string;
  purchaseOrderId: string;
  convertedCount: number;
  skipped: readonly { itemId: string; name: string; reason: string }[];
};

export type PurchaseOrdersClient = {
  /** Requires an idempotency key (send one via `options.idempotencyKey`). */
  create(body: PurchaseOrderCreateRequest, options?: RequestOptions): Promise<ApiResponse<PurchaseOrder>>;
  list(filters?: { status?: PurchaseOrderStatusWire }, pagination?: Pagination, options?: RequestOptions): Promise<Page<PurchaseOrder>>;
  read(poId: string, options?: RequestOptions): Promise<ApiResponse<PurchaseOrder>>;
  addItem(poId: string, body: PurchaseOrderItemAddRequest, options?: RequestOptions): Promise<ApiResponse<PurchaseOrderItem>>;
  updateItem(poId: string, itemId: string, body: PurchaseOrderItemUpdateRequest, options?: RequestOptions): Promise<ApiResponse<PurchaseOrderItem>>;
  removeItem(poId: string, itemId: string, options?: RequestOptions): Promise<ApiResponse<PurchaseOrderItem>>;
  order(poId: string, options?: RequestOptions): Promise<ApiResponse<PurchaseOrder>>;
  close(poId: string, options?: RequestOptions): Promise<ApiResponse<PurchaseOrder>>;
  cancel(poId: string, options?: RequestOptions): Promise<ApiResponse<PurchaseOrder>>;
  /** Owner/manager only. Returns the created purchase draft plus lines that could not be resolved. */
  toPurchase(poId: string, body?: { supplierId?: string }, options?: RequestOptions): Promise<ApiResponse<PoConvertResult>>;
};

export function createPurchaseOrdersClient(client: ApiClient): PurchaseOrdersClient {
  const root = '/purchase-orders';
  return {
    create: (body, options = {}) => client.post<PurchaseOrder>(root, body, options),
    list: ({ status } = {}, pagination = {}, options = {}) =>
      client.list<PurchaseOrder>(root, pagination, { ...options, query: { ...options.query, status } }),
    read: (poId, options = {}) => client.get<PurchaseOrder>(`${root}/${segment(poId)}`, options),
    addItem: (poId, body, options = {}) => client.post<PurchaseOrderItem>(`${root}/${segment(poId)}/items`, body, options),
    updateItem: (poId, itemId, body, options = {}) =>
      client.patch<PurchaseOrderItem>(`${root}/${segment(poId)}/items/${segment(itemId)}`, body, options),
    removeItem: (poId, itemId, options = {}) =>
      client.delete<PurchaseOrderItem>(`${root}/${segment(poId)}/items/${segment(itemId)}`, options),
    order: (poId, options = {}) => client.post<PurchaseOrder>(`${root}/${segment(poId)}/order`, undefined, options),
    close: (poId, options = {}) => client.post<PurchaseOrder>(`${root}/${segment(poId)}/close`, undefined, options),
    cancel: (poId, options = {}) => client.post<PurchaseOrder>(`${root}/${segment(poId)}/cancel`, undefined, options),
    toPurchase: (poId, body = {}, options = {}) => client.post<PoConvertResult>(`${root}/${segment(poId)}/to-purchase`, body, options),
  };
}

export type InventoryClient = {
  /** `storeId` is required by the endpoint; the token's branch is not assumed. */
  stock(storeId: string, options?: RequestOptions): Promise<ApiResponse<readonly StockRow[]>>;
  expiring(storeId: string, withinDays?: number, options?: RequestOptions): Promise<ApiResponse<readonly ExpiringBatch[]>>;
  /** Requires an idempotency key (send one via `options.idempotencyKey`). */
  receiveBatch(body: ReceiveBatchRequest, options?: RequestOptions): Promise<ApiResponse<unknown>>;
  intake(body: InventoryIntakeRequest, options?: RequestOptions): Promise<ApiResponse<InventoryIntake>>;
};

export function createInventoryClient(client: ApiClient): InventoryClient {
  return {
    stock: (storeId, options = {}) => client.request<readonly StockRow[]>('/inventory/stock', { method: 'GET' }, { ...options, query: { ...options.query, storeId } }),
    expiring: (storeId, withinDays = 30, options = {}) =>
      client.get<readonly ExpiringBatch[]>('/inventory/expiring', { ...options, query: { ...options.query, storeId, withinDays } }),
    // `/inventory/receive`, not `/inventory/batches`: the latter has never existed,
    // so every receive from the web app was a 404.
    receiveBatch: (body, options = {}) => client.post<unknown>('/inventory/receive', body, options),
    intake: (body, options = {}) => client.post<InventoryIntake>('/inventory/intakes', body, options),
  };
}

export type PurchaseStatus = 'draft' | 'confirmed' | 'returned';
export type PurchaseItem = { id: string; purchaseId: string; storeProductId: string; quantity: string; batchNumber: string; expiryDate?: string | null; unitCost?: string | null };
export type Purchase = {
  id: string;
  organizationId: string;
  storeId: string;
  supplierId: string;
  status: PurchaseStatus;
  invoiceNumber?: string | null;
  note?: string | null;
  purchasedAt: string;
  confirmedAt?: string | null;
  totalAmount?: string | null;
  items: readonly PurchaseItem[];
};
export type PurchaseCreateRequest = {
  supplierId: string;
  invoiceNumber?: string;
  note?: string;
  purchasedAt?: string;
  items: readonly { storeProductId: string; quantity: string; unitCost: string; batchNumber: string; expiryDate?: string }[];
};

export type PurchasesClient = {
  list(pagination?: Pagination, options?: RequestOptions): Promise<Page<Purchase>>;
  read(purchaseId: string, options?: RequestOptions): Promise<ApiResponse<Purchase>>;
  create(body: PurchaseCreateRequest, options?: RequestOptions): Promise<ApiResponse<Purchase>>;
  confirm(purchaseId: string, options?: RequestOptions): Promise<ApiResponse<Purchase>>;
};

export function createPurchasesClient(client: ApiClient): PurchasesClient {
  return {
    list: (pagination = {}, options = {}) => client.list<Purchase>('/purchases', pagination, options),
    read: (purchaseId, options = {}) => client.get<Purchase>(`/purchases/${segment(purchaseId)}`, options),
    create: (body, options = {}) => client.post<Purchase>('/purchases', body, options),
    confirm: (purchaseId, options = {}) => client.post<Purchase>(`/purchases/${segment(purchaseId)}/confirm`, undefined, options),
  };
}

export type Supplier = { id: string; organizationId: string; name: string; phone?: string | null; address?: string | null; status: string; createdAt: string };

export type SuppliersClient = {
  list(pagination?: Pagination, options?: RequestOptions): Promise<Page<Supplier>>;
  create(body: { name: string; phone?: string; address?: string }, options?: RequestOptions): Promise<ApiResponse<Supplier>>;
};

export function createSuppliersClient(client: ApiClient): SuppliersClient {
  return {
    list: (pagination = {}, options = {}) => client.list<Supplier>('/suppliers', pagination, options),
    create: (body, options = {}) => client.post<Supplier>('/suppliers', body, options),
  };
}

// --- Commerce phase: online orders, listings, prescriptions, public storefront ----

/** Mirrors `OrderStatus` in `backend/app/domains/orders.py` (values on the wire). */
export type OnlineOrderStatus =
  | 'pending'
  | 'reserved'
  | 'accepted'
  | 'preparing'
  | 'ready'
  | 'completed'
  | 'cancelled';

export type OnlineFulfillment = 'pickup' | 'delivery';

export type OrderItemLine = {
  id: string;
  storeProductId: string;
  productName: string;
  quantity: string;
  unitPrice: string;
  lineTotal: string;
};

export type OrderStatusHistoryEntry = {
  id: string;
  fromStatus: OnlineOrderStatus | null;
  toStatus: OnlineOrderStatus;
  actorUserId?: string | null;
  createdAt: string;
};

/** Mirrors `OrderResponse`. */
export type OnlineOrder = {
  id: string;
  organizationId: string;
  storeId: string;
  customerId?: string | null;
  status: OnlineOrderStatus;
  subtotal: string;
  total: string;
  prescriptionRequired: boolean;
  deliveryAddress?: Record<string, unknown> | null;
  createdAt: string;
  items: readonly OrderItemLine[];
  history: readonly OrderStatusHistoryEntry[];
};

export type OrderCheckoutItem = { storeProductId: string; quantity: string };

export type OrderCreateRequest = {
  items: readonly OrderCheckoutItem[];
  customerId?: string;
  fulfillment?: OnlineFulfillment;
  deliveryAddress?: Record<string, unknown>;
};

export type OrderTransitionRequest = { status: OnlineOrderStatus };
export type OrderListFilters = { status?: OnlineOrderStatus; customerId?: string };

/**
 * Typed client for `/orders` (`backend/app/routers/orders.py`).
 *
 * Staff-authenticated: creating and moving orders is counter work. Guest
 * traffic goes through `StorefrontClient` instead.
 */
export type OrdersClient = {
  list(filters?: OrderListFilters, options?: RequestOptions): Promise<ApiResponse<readonly OnlineOrder[]>>;
  read(orderId: string, options?: RequestOptions): Promise<ApiResponse<OnlineOrder>>;
  /** Requires an idempotency key (send one via `options.idempotencyKey`). */
  create(body: OrderCreateRequest, options?: RequestOptions): Promise<ApiResponse<OnlineOrder>>;
  transition(orderId: string, body: OrderTransitionRequest, options?: RequestOptions): Promise<ApiResponse<OnlineOrder>>;
};

export function createOrdersClient(client: ApiClient): OrdersClient {
  return {
    list: (filters = {}, options = {}) =>
      client.request<readonly OnlineOrder[]>('/orders', { method: 'GET' }, {
        ...options,
        query: { ...options.query, status: filters.status, customerId: filters.customerId },
      }),
    read: (orderId, options = {}) => client.get<OnlineOrder>(`/orders/${segment(orderId)}`, options),
    create: (body, options = {}) => client.post<OnlineOrder>('/orders', body, options),
    transition: (orderId, body, options = {}) =>
      client.post<OnlineOrder>(`/orders/${segment(orderId)}/transition`, body, options),
  };
}

/** Mirrors `StorefrontResponse`. */
export type Storefront = {
  id: string;
  organizationId: string;
  storeId: string;
  slug: string;
  displayName: string;
  enabled: boolean;
  customDomain?: string | null;
  createdAt: string;
};

export type StorefrontUpsertRequest = {
  slug: string;
  displayName: string;
  enabled?: boolean;
  customDomain?: string;
};

/** Mirrors `ListingResponse`: the per-branch online overlay of a store product. */
export type EcommerceListing = {
  id: string;
  storeId: string;
  storeProductId: string;
  onlineName?: string | null;
  description?: string | null;
  onlinePrice?: string | null;
  listed: boolean;
  pickupEnabled: boolean;
  deliveryEnabled: boolean;
};

export type ListingUpsertRequest = {
  onlineName?: string;
  description?: string;
  onlinePrice?: string;
  listed?: boolean;
  pickupEnabled?: boolean;
  deliveryEnabled?: boolean;
};

/** Typed client for `/ecommerce` (`backend/app/routers/ecommerce.py`). */
export type EcommerceClient = {
  listStorefronts(options?: RequestOptions): Promise<ApiResponse<readonly Storefront[]>>;
  upsertStorefront(body: StorefrontUpsertRequest, options?: RequestOptions): Promise<ApiResponse<Storefront>>;
  upsertListing(storeProductId: string, body: ListingUpsertRequest, options?: RequestOptions): Promise<ApiResponse<EcommerceListing>>;
  listListings(listed?: boolean, options?: RequestOptions): Promise<ApiResponse<readonly EcommerceListing[]>>;
  catalogue(slug: string, options?: RequestOptions): Promise<ApiResponse<readonly PublicCatalogueItem[]>>;
};

export function createEcommerceClient(client: ApiClient): EcommerceClient {
  return {
    listStorefronts: (options = {}) => client.get<readonly Storefront[]>('/ecommerce/storefronts', options),
    upsertStorefront: (body, options = {}) => client.post<Storefront>('/ecommerce/storefronts', body, options),
    upsertListing: (storeProductId, body, options = {}) =>
      client.put<EcommerceListing>(`/ecommerce/products/${segment(storeProductId)}/listing`, body, options),
    listListings: (listed, options = {}) =>
      client.get<readonly EcommerceListing[]>('/ecommerce/listings', { ...options, query: { ...options.query, listed } }),
    catalogue: (slug, options = {}) =>
      client.get<readonly PublicCatalogueItem[]>(`/ecommerce/storefronts/${segment(slug)}/catalogue`, options),
  };
}

/** Mirrors `PublicCatalogueItem`: one row of an anonymous storefront listing. */
export type PublicCatalogueItem = {
  storeProductId: string;
  name: string;
  price: string;
  pickupEnabled: boolean;
  deliveryEnabled: boolean;
  prescriptionRequired: boolean;
};

export type GuestCheckoutResult = OnlineOrder;

const ANONYMOUS_REQUEST: RequestOptions = { anonymous: true };

/**
 * Anonymous storefront surface (`backend/app/routers/storefront.py`).
 *
 * The organization slug plus storefront slug is the whole tenant address, so a
 * guest checkout can never be aimed at the wrong branch by stale local state.
 */
export type StorefrontClient = {
  catalogue(organizationSlug: string, slug: string, options?: RequestOptions): Promise<ApiResponse<readonly PublicCatalogueItem[]>>;
  /** Requires an idempotency key (send one via `options.idempotencyKey`). */
  checkout(
    organizationSlug: string,
    slug: string,
    body: OrderCreateRequest,
    options?: RequestOptions,
  ): Promise<ApiResponse<GuestCheckoutResult>>;
};

export function createStorefrontClient(client: ApiClient): StorefrontClient {
  return {
    catalogue: (organizationSlug, slug, options = {}) =>
      client.get<readonly PublicCatalogueItem[]>(
        `/storefronts/${segment(organizationSlug)}/${segment(slug)}/catalogue`,
        { ...ANONYMOUS_REQUEST, ...options },
      ),
    checkout: (organizationSlug, slug, body, options = {}) =>
      client.post<GuestCheckoutResult>(
        `/storefronts/${segment(organizationSlug)}/${segment(slug)}/orders`,
        body,
        { ...ANONYMOUS_REQUEST, ...options },
      ),
  };
}

export type PrescriptionStatusWire = 'pending' | 'approved' | 'rejected' | 'needs_clarification';

export type PrescriptionFileMeta = {
  id: string;
  objectKey: string;
  contentType: string;
  checksum: string;
  uploadedAt: string;
};

export type PrescriptionReviewEntry = {
  id: string;
  prescriptionId: string;
  status: PrescriptionStatusWire;
  pharmacistUserId: string;
  notes?: string | null;
  reviewedAt: string;
};

/** Mirrors `PrescriptionResponse`; files ride along with each prescription. */
export type Prescription = {
  id: string;
  organizationId: string;
  customerId?: string | null;
  orderId?: string | null;
  status: PrescriptionStatusWire;
  prescriberName?: string | null;
  prescriptionNumber?: string | null;
  expiresAt?: string | null;
  createdAt: string;
  files: readonly PrescriptionFileMeta[];
};

export type PrescriptionCreateRequest = {
  customerId?: string;
  orderId?: string;
  prescriberName?: string;
  prescriptionNumber?: string;
  expiresAt?: string;
};

export type PrescriptionFileRequest = { objectKey: string; contentType: string; checksum: string };
export type PrescriptionReviewRequest = { status: PrescriptionStatusWire; notes?: string };
export type PrescriptionAttachRequest = { orderId: string };
export type PrescriptionListFilters = { status?: PrescriptionStatusWire; customerId?: string; orderId?: string };

/** Typed client for `/prescriptions` (`backend/app/routers/prescriptions.py`). */
export type PrescriptionsClient = {
  list(filters?: PrescriptionListFilters, options?: RequestOptions): Promise<ApiResponse<readonly Prescription[]>>;
  create(body: PrescriptionCreateRequest, options?: RequestOptions): Promise<ApiResponse<Prescription>>;
  addFile(prescriptionId: string, body: PrescriptionFileRequest, options?: RequestOptions): Promise<ApiResponse<Prescription>>;
  review(prescriptionId: string, body: PrescriptionReviewRequest, options?: RequestOptions): Promise<ApiResponse<PrescriptionReviewEntry>>;
  attachToOrder(prescriptionId: string, body: PrescriptionAttachRequest, options?: RequestOptions): Promise<ApiResponse<Prescription>>;
};

export function createPrescriptionsClient(client: ApiClient): PrescriptionsClient {
  return {
    list: (filters = {}, options = {}) =>
      client.get<readonly Prescription[]>('/prescriptions', {
        ...options,
        query: { ...options.query, status: filters.status, customerId: filters.customerId, orderId: filters.orderId },
      }),
    create: (body, options = {}) => client.post<Prescription>('/prescriptions', body, options),
    addFile: (prescriptionId, body, options = {}) =>
      client.post<Prescription>(`/prescriptions/${segment(prescriptionId)}/files`, body, options),
    review: (prescriptionId, body, options = {}) =>
      client.post<PrescriptionReviewEntry>(`/prescriptions/${segment(prescriptionId)}/review`, body, options),
    attachToOrder: (prescriptionId, body, options = {}) =>
      client.post<Prescription>(`/prescriptions/${segment(prescriptionId)}/order`, body, options),
  };
}

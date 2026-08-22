export { ApiClient } from './client';
export type { ApiClientConfig, ApiTransport, QueryValue, RequestOptions, RetryEvent } from './client';

export {
  ApiRequestError,
  clientErrorCodes,
  decodeApiError,
  errorStatus,
  isApiRequestError,
  isRetryableErrorCode,
  isRetryableStatus,
  isServerErrorCode,
  serverErrorCodes,
  statusForErrorCode,
  timeoutError,
  toApiRequestError,
} from './errors';
export type { ApiErrorCode, ClientErrorCode, ServerErrorCode } from './errors';

export {
  assertIdempotencyKey,
  createIdempotencyKey,
  defaultRetryPolicy,
  defaultSleep,
  defaultTimeoutMs,
  idempotencyKeyMaxLength,
  idempotencyKeyMinLength,
  isIdempotentMethod,
  isSafeMethod,
  isValidIdempotencyKey,
  methodOf,
  retryDelayMs,
  shouldRetry,
} from './policy';
export type { RetryContext, RetryDecision, RetryPolicy, Sleep } from './policy';

export {
  clampLimit,
  collectPages,
  CursorStore,
  decodePage,
  defaultPageLimit,
  hasMore,
  maxPageLimit,
  nextPagination,
  paginationQuery,
} from './pagination';
export type { CollectOptions, Page, PageFetcher } from './pagination';

export { createMemoryStorage, storageKeys } from './storage';
export type { StorageAdapter } from './storage';

export { createFetchTransport } from './transport';
export type { FetchLike, FetchTransportConfig } from './transport';

export {
  createAuthClient,
  createOrganizationsClient,
  createPharmacyApi,
  createResourceClient,
  createStoresClient,
  createUsersClient,
} from './resources';
export type {
  AuthClient,
  AuthSession,
  CurrentOrganization,
  CurrentUser,
  Device,
  DeviceClaim,
  DeviceRegisterRequest,
  DeviceStatus,
  LogoutResult,
  MembershipOption,
  OrganizationCreateRequest,
  OrganizationCreateResponse,
  OrganizationProfile,
  OrganizationSettings,
  OrganizationSettingsResponse,
  OrganizationSettingsUpdate,
  OrganizationUpdateRequest,
  OrganizationsClient,
  OtpChallenge,
  OtpPurpose,
  OtpRequest,
  OtpVerifyRequest,
  PharmacyApi,
  PinLoginRequest,
  PinSetRequest,
  PinStatus,
  RefreshRequest,
  ResourceClient,
  SelectContextRequest,
  SessionListFilters,
  StaffRole,
  StoreAssignmentRequest,
  StoreCreateRequest,
  StoreOperatingStatus,
  StoreProfile,
  StoreSettings,
  StoreSettingsResponse,
  StoreSettingsUpdate,
  StoreStatusUpdateRequest,
  StoreUpdateRequest,
  StoresClient,
  TokenBundle,
  UserCreateRequest,
  UserListFilters,
  UserProfile,
  UserRoleUpdateRequest,
  UserStatusUpdateRequest,
  UserUpdateRequest,
  UsersClient,
} from './resources';

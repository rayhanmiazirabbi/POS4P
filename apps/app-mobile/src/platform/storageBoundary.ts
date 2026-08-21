import type { StorageAdapter } from '@pharmacy/api';

export type SQLiteAdapter = {
  get(key: string): Promise<string | null>;
  set(key: string, value: string): Promise<void>;
  remove(key: string): Promise<void>;
};

export type SecureStoreAdapter = {
  getItem(key: string): Promise<string | null>;
  setItem(key: string, value: string): Promise<void>;
  deleteItem(key: string): Promise<void>;
};

export type MobileStorageAdapters = {
  sqlite: SQLiteAdapter;
  secureStore: SecureStoreAdapter;
};

export type MobileStorage = {
  credentials: StorageAdapter;
  offline: SQLiteAdapter;
};

export function createMobileStorage(adapters: MobileStorageAdapters): MobileStorage {
  return {
    credentials: {
      get: (key) => adapters.secureStore.getItem(key),
      set: (key, value) => adapters.secureStore.setItem(key, value),
      remove: (key) => adapters.secureStore.deleteItem(key),
    },
    offline: adapters.sqlite,
  };
}

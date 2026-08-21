import * as SecureStore from 'expo-secure-store';
import { openDatabaseAsync, type SQLiteDatabase } from 'expo-sqlite';
import type { SecureStoreAdapter, SQLiteAdapter } from './storageBoundary';

export type { MobileStorageAdapters, SQLiteAdapter, SecureStoreAdapter } from './storageBoundary';
export { createMobileStorage } from './storageBoundary';

export async function openMobileDatabase(name = 'pharmacy-platform.db'): Promise<SQLiteDatabase> {
  const database = await openDatabaseAsync(name);
  await database.execAsync('CREATE TABLE IF NOT EXISTS storage (key TEXT PRIMARY KEY NOT NULL, value TEXT NOT NULL)');
  return database;
}

export function createSQLiteAdapter(database: SQLiteDatabase): SQLiteAdapter {
  return {
    async get(key) {
      return (await database.getFirstAsync<{ value: string }>('SELECT value FROM storage WHERE key = ?', key))?.value ?? null;
    },
    async set(key, value) {
      await database.runAsync(
        'INSERT INTO storage (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value',
        key,
        value,
      );
    },
    async remove(key) {
      await database.runAsync('DELETE FROM storage WHERE key = ?', key);
    },
  };
}

export const secureStoreAdapter: SecureStoreAdapter = {
  getItem: (key) => SecureStore.getItemAsync(key),
  setItem: (key, value) => SecureStore.setItemAsync(key, value),
  deleteItem: (key) => SecureStore.deleteItemAsync(key),
};

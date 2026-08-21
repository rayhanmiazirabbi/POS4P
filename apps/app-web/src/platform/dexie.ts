import Dexie, { type Table } from 'dexie';

type StorageRecord = { key: string; value: string };

class BrowserDatabase extends Dexie {
  readonly storage!: Table<StorageRecord, string>;

  constructor() {
    super('pharmacy-platform');
    this.version(1).stores({ storage: 'key' });
  }
}

const database = new BrowserDatabase();

export type BrowserStorageAdapter = {
  get(key: string): Promise<string | null>;
  set(key: string, value: string): Promise<void>;
  remove(key: string): Promise<void>;
};

export const dexieStorage: BrowserStorageAdapter = {
  async get(key) {
    return (await database.storage.get(key))?.value ?? null;
  },
  async set(key, value) {
    await database.storage.put({ key, value });
  },
  async remove(key) {
    await database.storage.delete(key);
  },
};

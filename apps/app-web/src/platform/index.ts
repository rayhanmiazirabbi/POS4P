import type { StorageAdapter } from '@pharmacy/api';

import { dexieStorage } from './dexie';

export const browserStorage: StorageAdapter = dexieStorage;

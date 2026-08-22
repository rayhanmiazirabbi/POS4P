import type { Page } from '@pharmacy/core';
import type { Pagination } from '@pharmacy/types';

import type { StorageAdapter } from './storage';

/**
 * A page of results as the backend sends it (`app/schemas/base.py::Page`).
 * Re-exported from `@pharmacy/core` so both layers name the same shape.
 */
export type { Page } from '@pharmacy/core';

export const defaultPageLimit = 25;
export const maxPageLimit = 100;

/** Clamp to the range `PaginationParams` accepts server-side (1-100). */
export function clampLimit(limit: number | undefined): number {
  if (limit === undefined || !Number.isFinite(limit)) return defaultPageLimit;
  return Math.min(maxPageLimit, Math.max(1, Math.trunc(limit)));
}

/** Serialize pagination into the camelCase query the backend reads. */
export function paginationQuery(pagination: Pagination | undefined): Record<string, string> {
  const query: Record<string, string> = {};
  if (pagination?.cursor) query['cursor'] = pagination.cursor;
  if (pagination?.limit !== undefined) query['limit'] = String(clampLimit(pagination.limit));
  return query;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null;
}

/**
 * Decode a page defensively: a missing `nextCursor` means "no more pages", and
 * a missing `items` array yields an empty page rather than a crash mid-scroll.
 */
export function decodePage<T>(value: unknown): Page<T> {
  if (!isRecord(value)) return { items: [], nextCursor: null };
  const items = Array.isArray(value['items']) ? (value['items'] as T[]) : [];
  const rawCursor = value['nextCursor'];
  const nextCursor = typeof rawCursor === 'string' && rawCursor !== '' ? rawCursor : null;
  const rawTotal = value['total'];
  const page: Page<T> = { items, nextCursor };
  if (typeof rawTotal === 'number' && Number.isFinite(rawTotal)) page.total = rawTotal;
  return page;
}

export function hasMore<T>(page: Page<T>): boolean {
  return page.nextCursor !== null;
}

/** Advance to the next request, or `null` once the server stops sending a cursor. */
export function nextPagination<T>(page: Page<T>, current: Pagination = {}): Pagination | null {
  if (page.nextCursor === null) return null;
  const next: Pagination = { cursor: page.nextCursor };
  if (current.limit !== undefined) next.limit = current.limit;
  return next;
}

/**
 * Durable cursor for resumable listings and Stage 2 sync.
 *
 * Persisting through `StorageAdapter` keeps the client platform-neutral: the
 * mobile app can back it with SQLite and the web app with IndexedDB.
 */
export class CursorStore {
  constructor(
    private readonly storage: StorageAdapter,
    private readonly namespace = 'cursor',
  ) {}

  private keyFor(name: string): string {
    return `${this.namespace}:${name}`;
  }

  async read(name: string): Promise<string | null> {
    return this.storage.get(this.keyFor(name));
  }

  async write(name: string, cursor: string | null): Promise<void> {
    if (cursor === null) {
      await this.storage.remove(this.keyFor(name));
      return;
    }
    await this.storage.set(this.keyFor(name), cursor);
  }

  async clear(name: string): Promise<void> {
    await this.storage.remove(this.keyFor(name));
  }

  /** Resume where the last run stopped, keeping the caller's page size. */
  async resume(name: string, limit?: number): Promise<Pagination> {
    const cursor = await this.read(name);
    const pagination: Pagination = {};
    if (cursor !== null) pagination.cursor = cursor;
    if (limit !== undefined) pagination.limit = clampLimit(limit);
    return pagination;
  }

  /** Record progress after a page; a null `nextCursor` clears the checkpoint. */
  async advance<T>(name: string, page: Page<T>): Promise<void> {
    await this.write(name, page.nextCursor);
  }
}

export type PageFetcher<T> = (pagination: Pagination) => Promise<Page<T>>;

export type CollectOptions = {
  /** Hard stop so a server that keeps returning a cursor cannot loop forever. */
  maxPages?: number;
  limit?: number;
};

/**
 * Walk every page. Stops on a repeated cursor: the server is not advancing, and
 * re-requesting the same cursor would spin.
 */
export async function collectPages<T>(fetch: PageFetcher<T>, options: CollectOptions = {}): Promise<T[]> {
  const maxPages = options.maxPages ?? 100;
  const items: T[] = [];
  const seen = new Set<string>();
  let pagination: Pagination | null = options.limit === undefined ? {} : { limit: clampLimit(options.limit) };

  for (let index = 0; index < maxPages && pagination !== null; index += 1) {
    const page: Page<T> = await fetch(pagination);
    items.push(...page.items);
    const next: Pagination | null = nextPagination(page, pagination);
    if (next?.cursor !== undefined) {
      if (seen.has(next.cursor)) break;
      seen.add(next.cursor);
    }
    pagination = next;
  }
  return items;
}

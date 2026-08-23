import type { CurrentUser } from '@pharmacy/api';
import { beforeEach, describe, expect, it } from 'vitest';

import { desktopPlatform } from '../platform/runtime';
import { forgetTerminal, readTerminal, rememberTerminal, terminalStorageKey } from './terminal';

/** Outside Tauri `desktopPlatform().database` is a real (if dev-grade) store, so
 *  the binding round-trips through the same code path the packaged till uses. */
async function store() {
  return (await desktopPlatform()).database;
}

/** Only the four fields `rememberTerminal` reads matter; the rest of `CurrentUser`
 *  is filled with a cast so the fixture stays about the binding. */
function currentUser(overrides: Partial<CurrentUser> = {}): CurrentUser {
  return {
    organizationId: 'org-1',
    organizationName: 'Bismillah Pharmacy',
    storeId: 'store-1',
    storeName: 'Mirpur branch',
    ...overrides,
  } as CurrentUser;
}

describe('terminal binding', () => {
  beforeEach(async () => {
    await forgetTerminal();
  });

  it('round-trips the shop and branch a till was signed into', async () => {
    await rememberTerminal(currentUser());
    expect(await readTerminal()).toEqual({
      organizationId: 'org-1',
      organizationName: 'Bismillah Pharmacy',
      storeId: 'store-1',
      storeName: 'Mirpur branch',
    });
  });

  it('reads an unbound till as null', async () => {
    expect(await readTerminal()).toBeNull();
  });

  it('keeps the binding usable when the token had no store', async () => {
    // A multi-branch owner can hold a token with no store pinned. PIN login sends
    // `storeId` only when there is one, so the binding has to distinguish "no
    // branch" from "some branch" rather than flatten both to a blank string.
    await rememberTerminal(currentUser({ storeId: null, storeName: null }));
    expect(await readTerminal()).toMatchObject({ organizationId: 'org-1', storeId: null, storeName: null });
  });

  it('treats a missing organization as unbound rather than as a hole', async () => {
    // The organization id is what `POST /auth/pin/login` requires. A binding
    // without one would be sent as an empty tenant and answered with a generic
    // login failure, which reads to the cashier as a wrong PIN. Falling back to
    // the SMS path says what is actually true: this till is not bound yet.
    await (await store()).set(terminalStorageKey, JSON.stringify({ organizationName: 'Bismillah Pharmacy', storeId: 'store-1' }));
    expect(await readTerminal()).toBeNull();
  });

  it('treats an unparseable or wrong-shaped blob as unbound', async () => {
    for (const raw of ['not json', '[]', 'null', '"org-1"', '{"organizationId":"   ","organizationName":"x"}']) {
      await (await store()).set(terminalStorageKey, raw);
      expect(await readTerminal(), raw).toBeNull();
    }
  });

  it('overwrites the branch when an admin moves the till', async () => {
    await rememberTerminal(currentUser());
    await rememberTerminal(currentUser({ storeId: 'store-2', storeName: 'Uttara branch' }));
    expect(await readTerminal()).toMatchObject({ storeId: 'store-2', storeName: 'Uttara branch' });
  });

  it('forgets on request, which is what sends the next cashier back to an SMS code', async () => {
    await rememberTerminal(currentUser());
    await forgetTerminal();
    expect(await readTerminal()).toBeNull();
  });
});

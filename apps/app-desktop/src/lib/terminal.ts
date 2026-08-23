import type { CurrentUser } from '@pharmacy/api';

import { desktopPlatform } from '../platform/runtime';

/**
 * Which shop and branch this machine is a till for.
 *
 * `POST /auth/pin/login` requires an `organizationId`: a four-digit PIN is not a
 * global identifier and the backend will not guess a tenant from one. So PIN
 * entry is an *unlock* of a till that already knows where it stands, not a login
 * from nothing -- and something has to remember where it stands. That is this.
 *
 * Stored beside `device_key` rather than with the tokens, and deliberately kept
 * across sign-out: a till does not change shops at a shift change. Clearing it
 * is `forgetTerminal`, an explicit act, because it is what sends the next
 * cashier back to an SMS code.
 */
export type TerminalBinding = {
  organizationId: string;
  organizationName: string;
  storeId: string | null;
  storeName: string | null;
};

const TERMINAL_KEY = 'desktop_terminal_v1';

/** Exported for the tests, which write malformed blobs under it on purpose. */
export const terminalStorageKey = TERMINAL_KEY;

function isNonEmptyString(value: unknown): value is string {
  return typeof value === 'string' && value.trim() !== '';
}

/** Decoded defensively: a hand-edited or half-written store entry must read as
 *  "not bound" and fall back to the OTP path, never as a binding with holes in
 *  it that PIN login would send to the server as an empty organization. */
function decode(raw: string): TerminalBinding | null {
  let value: unknown;
  try {
    value = JSON.parse(raw);
  } catch {
    return null;
  }
  if (typeof value !== 'object' || value === null) return null;
  const row = value as Record<string, unknown>;
  if (!isNonEmptyString(row.organizationId) || !isNonEmptyString(row.organizationName)) return null;
  return {
    organizationId: row.organizationId,
    organizationName: row.organizationName,
    storeId: isNonEmptyString(row.storeId) ? row.storeId : null,
    storeName: isNonEmptyString(row.storeName) ? row.storeName : null,
  };
}

export async function readTerminal(): Promise<TerminalBinding | null> {
  const raw = await (await desktopPlatform()).database.get(TERMINAL_KEY);
  return raw === null ? null : decode(raw);
}

/**
 * Record the shop after a successful sign-in, from the live rows `GET /auth/me`
 * returns rather than from token claims.
 *
 * Failure is swallowed: the session is already established and the cashier is
 * selling. Losing the binding costs the next cashier an SMS code, which is a
 * lesser harm than an error banner on a till that just signed in successfully.
 */
export async function rememberTerminal(user: CurrentUser): Promise<void> {
  const binding: TerminalBinding = {
    organizationId: user.organizationId,
    organizationName: user.organizationName,
    storeId: user.storeId ?? null,
    storeName: user.storeName ?? null,
  };
  try {
    await (await desktopPlatform()).database.set(TERMINAL_KEY, JSON.stringify(binding));
  } catch {
    // See above.
  }
}

export async function forgetTerminal(): Promise<void> {
  await (await desktopPlatform()).database.remove(TERMINAL_KEY);
}

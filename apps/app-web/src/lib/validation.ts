'use client';

import { z } from 'zod';

/**
 * Field formats shared by the operational forms.
 *
 * The backend stores money as decimal strings and quantities as whole units, and
 * every refusal it sends back arrives after the fact -- by which point the
 * cashier has moved on. Catching the format here keeps a mistyped "10..00" from
 * becoming a round trip.
 */

/** The first human-readable refusal from a field check, or null when it passes. */
export function fieldIssue<T>(result: z.SafeParseReturnType<T, T>): string | null {
  return result.success ? null : result.error.issues[0]?.message ?? 'Invalid input';
}

/** A non-negative money amount, e.g. `250` or `250.50`. */
export const decimalAmount = z
  .string()
  .trim()
  .regex(/^\d+(\.\d{1,2})?$/, 'Enter a plain amount, e.g. 250 or 250.50');

/** A count of units: whole, greater than zero, no separators. */
export const positiveQuantity = z
  .string()
  .trim()
  .regex(/^[1-9]\d*$/, 'Enter a whole number above zero');

/** A phone number as staff type it: optional `+`, then digits only (E.164-ish). */
export const phoneNumber = z
  .string()
  .trim()
  .regex(/^\+?\d{8,15}$/, 'Enter digits only, e.g. +8801700000001');

/**
 * What a POS screen is allowed to know about the camera.
 *
 * `app-mobile.md` requires camera access through an adapter, and this is the half
 * of that rule with teeth: nothing above this file imports `expo-camera`, so the
 * screen stays testable in a plain vitest run with no native module to mock, and a
 * change of scanner library is a change to `nativeScanner.ts` alone.
 *
 * The permission states are modelled rather than reduced to a boolean because the
 * counter has to say something different for each. `denied` is recoverable in
 * Settings; `unavailable` -- a device with no usable camera -- is not, and a screen
 * that keeps offering "grant access" for it sends a cashier looking for a switch
 * that does not exist.
 */
export type ScannerPermission = 'granted' | 'denied' | 'undetermined' | 'unavailable';

export type Scanner = {
  /** Whether this device can scan at all, asked before any camera UI is shown. */
  status(): Promise<ScannerPermission>;
  /** Ask the user. Answering `undetermined` is not possible; it resolves either way. */
  request(): Promise<ScannerPermission>;
};

/**
 * The barcode symbologies a pharmacy counter actually meets.
 *
 * EAN-13 and EAN-8 are the retail codes on a medicine carton, UPC-A covers
 * imports, and Code 128 is what a wholesaler's own labels are printed in. QR is
 * deliberately absent: scanning one would hand `matchShelf` a URL, which cannot
 * match a barcode and would land in the ambiguous branch for no reason.
 */
export const scannerFormats = ['ean13', 'ean8', 'upc_a', 'upc_e', 'code128', 'code39'] as const;

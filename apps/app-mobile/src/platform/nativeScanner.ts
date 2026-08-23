import { Camera, CameraView } from 'expo-camera';

import type { Scanner, ScannerPermission } from './scannerBoundary';

/**
 * `expo-camera`, behind the scanner boundary.
 *
 * The only file in the app that imports the camera library, matching how
 * `nativeAdapters.ts` is the only one that imports `expo-sqlite`. Everything above
 * takes a `Scanner`, so the POS screen has no native import in its module graph.
 *
 * `app.json` configures this plugin with `microphonePermission: false` and
 * `recordAudioAndroid: false`, which cannot be commented there because it is JSON.
 * The reason: `expo-camera` requests both by default for video recording, and a
 * point-of-sale app that asks a pharmacist for the microphone is asking for
 * something it never uses. Scanning needs the camera and nothing else.
 */
function toPermission(response: { granted: boolean; canAskAgain: boolean; status: string }): ScannerPermission {
  if (response.granted) return 'granted';
  // `undetermined` is the state where asking is still worth offering; a denial the
  // user cannot revisit in-app is reported as denied so the screen says "Settings"
  // rather than showing a button that will never open a prompt again.
  return response.status === 'undetermined' && response.canAskAgain ? 'undetermined' : 'denied';
}

export const nativeScanner: Scanner = {
  async status() {
    // Availability is checked first and separately. A denied permission can be
    // fixed in Settings; a device with no camera cannot, and telling a cashier to
    // grant access to hardware that is not there is worse than saying so.
    if (!(await CameraView.isAvailableAsync())) return 'unavailable';
    return toPermission(await Camera.getCameraPermissionsAsync());
  },

  async request() {
    if (!(await CameraView.isAvailableAsync())) return 'unavailable';
    return toPermission(await Camera.requestCameraPermissionsAsync());
  },
};

export { CameraView };

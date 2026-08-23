export type TauriCommand<Arguments extends unknown[], Result> = (...args: Arguments) => Promise<Result>;

/**
 * The durable key-value store the shell persists to. Inside Tauri this is a
 * SQLite database via the `store_*` commands; the browser dev fallback is
 * localStorage, so the name stays backend-neutral.
 */
export type LocalStore = {
  get(key: string): Promise<string | null>;
  set(key: string, value: string): Promise<void>;
  remove(key: string): Promise<void>;
};

export type HardwareAdapters = {
  printReceipt: TauriCommand<[receipt: string], { ok: true } | { ok: false; reason: string }>;
  scan: TauriCommand<[], string | null>;
  openCashDrawer: TauriCommand<[], { ok: true } | { ok: false; reason: string }>;
};

export type DesktopPlatform = {
  database: LocalStore;
  hardware: HardwareAdapters;
};

export function createDesktopPlatform(platform: DesktopPlatform): DesktopPlatform {
  return platform;
}

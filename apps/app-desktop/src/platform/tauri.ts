export type TauriCommand<Arguments extends unknown[], Result> = (...args: Arguments) => Promise<Result>;

export type SqliteStore = {
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
  database: SqliteStore;
  hardware: HardwareAdapters;
};

export function createDesktopPlatform(platform: DesktopPlatform): DesktopPlatform {
  return platform;
}

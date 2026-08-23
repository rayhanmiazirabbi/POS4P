import type { DesktopPlatform, HardwareAdapters, LocalStore } from './tauri';

/**
 * Runtime platform binding. Inside Tauri the commands come from Rust; in a
 * plain browser (dev) the same surface is served from localStorage and DOM
 * fallbacks so the POS logic never branches on the environment.
 */

const memory = new Map<string, string>();

let durable: Storage | null | undefined;

/**
 * `localStorage`, but only if it actually works.
 *
 * Existence and usability are different things, and the previous
 * `globalThis.localStorage?.setItem(...)` only guarded the first. Node exposes a
 * `localStorage` object with no methods on it unless the process was started with
 * a backing file, and browsers throw on access outright when site storage is
 * disabled -- so the optional chain passed and the call threw, from inside the
 * code path whose entire job is to not lose a queued sale.
 *
 * The probe result is cached, including the negative: a `setItem`/`removeItem`
 * round trip per read would be absurd, and this only ever runs outside Tauri.
 */
function durableStore(): Storage | null {
  if (durable !== undefined) return durable;
  durable = null;
  try {
    const candidate = globalThis.localStorage as Storage | undefined;
    if (typeof candidate?.getItem !== 'function' || typeof candidate.setItem !== 'function' || typeof candidate.removeItem !== 'function') return durable;
    const probe = '__pharmacy_storage_probe__';
    candidate.setItem(probe, probe);
    candidate.removeItem(probe);
    durable = candidate;
  } catch {
    // Quota exhausted, or storage blocked by policy. Either way it is not usable.
  }
  return durable;
}

/**
 * Dev-only fallback. Inside Tauri the Rust store owns persistence and reports
 * write failures; here `memory` is a last resort with no durability at all, so a
 * browser with storage switched off keeps the queue only until the tab closes.
 * That is acceptable because no packaged till runs this path.
 */
const browserStore: LocalStore = {
  async get(key) {
    return durableStore()?.getItem(key) ?? memory.get(key) ?? null;
  },
  async set(key, value) {
    memory.set(key, value);
    durableStore()?.setItem(key, value);
  },
  async remove(key) {
    memory.delete(key);
    durableStore()?.removeItem(key);
  },
};

const browserHardware: HardwareAdapters = {
  async printReceipt(receipt) {
    // Fallback path: render the receipt text and let the OS print dialog do the rest.
    const window_ = globalThis.open('', '_blank', 'width=380,height=600');
    if (window_ === null) return { ok: false, reason: 'Print window blocked' };
    window_.document.write(`<pre style="font-family:ui-monospace,monospace">${receipt.replace(/[&<>]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' })[c] ?? c)}</pre>`);
    window_.document.close();
    window_.print();
    return { ok: true };
  },
  async scan() {
    return null; // scanners deliver keystrokes to the focused field; no command needed
  },
  async openCashDrawer() {
    return { ok: false, reason: 'Cash drawer not connected' };
  },
};

async function tauriPlatform(): Promise<DesktopPlatform | null> {
  const internals = (globalThis as { __TAURI_INTERNALS__?: unknown }).__TAURI_INTERNALS__;
  if (internals === undefined) return null;
  const { invoke } = await import('@tauri-apps/api/core');
  // Rust commands return `Result<T, String>`; an `Err` rejects the invoke
  // promise. The typed adapter surface stays `{ ok, reason }` for the UI.
  async function okOrReason<T>(command: string, args: Record<string, unknown>): Promise<{ ok: true; value: T } | { ok: false; reason: string }> {
    try {
      return { ok: true, value: await invoke<T>(command, args) };
    } catch (cause) {
      return { ok: false, reason: String(cause) };
    }
  }
  return {
    database: {
      get: (key) => invoke<string | null>('store_get', { key }),
      set: (key, value) => invoke<void>('store_set', { key, value }),
      remove: (key) => invoke<void>('store_remove', { key }),
    },
    hardware: {
      printReceipt: async (receipt) => {
        const result = await okOrReason<void>('print_receipt', { receipt });
        return result.ok ? { ok: true } : { ok: false, reason: result.reason };
      },
      scan: async () => {
        const result = await okOrReason<string | null>('scan', {});
        return result.ok ? result.value : null;
      },
      openCashDrawer: async () => {
        const result = await okOrReason<void>('open_cash_drawer', {});
        return result.ok ? { ok: true } : { ok: false, reason: result.reason };
      },
    },
  };
}

let resolved: Promise<DesktopPlatform> | null = null;

export function desktopPlatform(): Promise<DesktopPlatform> {
  resolved ??= tauriPlatform().then((platform) => platform ?? { database: browserStore, hardware: browserHardware });
  return resolved;
}

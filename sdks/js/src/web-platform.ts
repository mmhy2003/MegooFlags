// What the SDK uses from a browser.
import type { StorageLike } from "./platform.js";

const PROBE = "megooflags-probe";

/**
 * localStorageOrNull returns localStorage when it can be written, else null:
 * server rendering has none, and private modes or blocked storage throw on
 * access or on write.
 */
export function localStorageOrNull(): StorageLike | null {
  try {
    const storage = (globalThis as { localStorage?: Storage }).localStorage;
    if (!storage) return null;
    storage.setItem(PROBE, PROBE);
    storage.removeItem(PROBE);
    return storage;
  } catch {
    return null;
  }
}

import type { Logger } from "@megooflags/js";
import type { StorageLike } from "@megooflags/js/platform";

export const silent: Logger = {
  debug: () => {},
  info: () => {},
  warn: () => {},
  error: () => {},
};

/** An in-memory store with the asynchronous shape of AsyncStorage. */
export class FakeAsyncStorage implements StorageLike {
  readonly items = new Map<string, string>();
  getItem(key: string): Promise<string | null> {
    return Promise.resolve(this.items.get(key) ?? null);
  }
  setItem(key: string, value: string): Promise<void> {
    this.items.set(key, value);
    return Promise.resolve();
  }
  removeItem(key: string): Promise<void> {
    this.items.delete(key);
    return Promise.resolve();
  }
}

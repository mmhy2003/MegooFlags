// Kept apart from provider.test.ts: Node's fetch there leaves keep-alive
// sockets whose own timers would be counted under fake timers here.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MegooFlagsWebProvider } from "../src/index.js";
import type { Logger } from "../src/internal/transport.js";
import type { Platform, StorageLike } from "../src/platform.js";

const KEY = "mf_client_secret123";

const silent: Logger = {
  debug: () => {},
  info: () => {},
  warn: () => {},
  error: () => {},
};

const result = {
  key: "f",
  value: true,
  variant: "true",
  reason: "TARGETING_MATCH",
  error_code: null,
  error_message: null,
};

class MemoryStorage implements StorageLike {
  readonly items = new Map<string, string>();
  getItem(key: string): string | null {
    return this.items.get(key) ?? null;
  }
  setItem(key: string, value: string): void {
    this.items.set(key, value);
  }
  removeItem(key: string): void {
    this.items.delete(key);
  }
}

describe("MegooFlagsWebProvider close", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("can close twice and leaves no timers behind", async () => {
    const fetchFn = (() =>
      Promise.resolve(
        new Response(JSON.stringify({ results: [result] }), {
          status: 200,
        }),
      )) as typeof fetch;
    const openStream: Platform["openStream"] = (_url, _headers, signal) =>
      Promise.resolve({
        status: 200,
        header: () => null,
        body: {
          [Symbol.asyncIterator]: () => ({
            next: () =>
              new Promise<IteratorResult<Uint8Array>>((resolve) =>
                signal.addEventListener("abort", () =>
                  resolve({ done: true, value: undefined }),
                ),
              ),
          }),
        },
      });
    const p = new MegooFlagsWebProvider(
      {
        sdkKey: KEY,
        baseUrl: "https://flags.example.com",
        fetch: fetchFn,
        logger: silent,
      },
      { openStream, storage: new MemoryStorage(), background: null },
    );

    await p.initialize({ targetingKey: "user-1" });
    await vi.advanceTimersByTimeAsync(0);
    expect(p.resolveBooleanEvaluation("f", false).value).toBe(true);
    await p.onClose();
    await p.onClose();

    expect(vi.getTimerCount()).toBe(0);
  });
});

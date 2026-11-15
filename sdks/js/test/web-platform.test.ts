import { afterEach, describe, expect, it, vi } from "vitest";
import { localStorageOrNull } from "../src/web-platform.js";

const memoryStorage = () => {
  const items = new Map<string, string>();
  return {
    items,
    getItem: (k: string) => items.get(k) ?? null,
    setItem: (k: string, v: string) => void items.set(k, v),
    removeItem: (k: string) => void items.delete(k),
  };
};

describe("localStorageOrNull", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("is null without localStorage (server rendering)", () => {
    vi.stubGlobal("localStorage", undefined);
    expect(localStorageOrNull()).toBeNull();
  });

  it("returns localStorage when the probe works, and leaves nothing behind", () => {
    const storage = memoryStorage();
    vi.stubGlobal("localStorage", storage);
    expect(localStorageOrNull()).toBe(storage);
    expect(storage.items.size).toBe(0);
  });

  it("is null when setItem throws (private mode)", () => {
    vi.stubGlobal("localStorage", {
      ...memoryStorage(),
      setItem: () => {
        throw new Error("QuotaExceededError");
      },
    });
    expect(localStorageOrNull()).toBeNull();
  });

  it("is null when reading localStorage throws (blocked storage)", () => {
    const original = Object.getOwnPropertyDescriptor(
      globalThis,
      "localStorage",
    );
    Object.defineProperty(globalThis, "localStorage", {
      configurable: true,
      get() {
        throw new Error("SecurityError");
      },
    });
    try {
      expect(localStorageOrNull()).toBeNull();
    } finally {
      if (original) Object.defineProperty(globalThis, "localStorage", original);
      else delete (globalThis as { localStorage?: unknown }).localStorage;
    }
  });
});

// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import { webPlatform as fromPlatformEntry } from "../src/platform.js";
import type { BackgroundState } from "../src/platform.js";
import {
  localStorageOrNull,
  webBackground,
  webPlatform,
} from "../src/web-platform.js";

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

describe("webBackground", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    delete (document as { visibilityState?: unknown }).visibilityState;
  });

  const setVisibility = (state: DocumentVisibilityState) =>
    Object.defineProperty(document, "visibilityState", {
      configurable: true,
      get: () => state,
    });

  it("is null without a document (server rendering)", () => {
    vi.stubGlobal("document", undefined);
    expect(webBackground()).toBeNull();
  });

  it("reads the current state from document.visibilityState", () => {
    const signal = webBackground()!;
    setVisibility("hidden");
    expect(signal.current()).toBe("hidden");
    setVisibility("visible");
    expect(signal.current()).toBe("visible");
  });

  it("reports visibilitychange, pagehide and pageshow", () => {
    const states: BackgroundState[] = [];
    const unsubscribe = webBackground()!.subscribe((s) => states.push(s));

    setVisibility("hidden");
    document.dispatchEvent(new Event("visibilitychange"));
    window.dispatchEvent(new Event("pagehide"));
    window.dispatchEvent(new Event("pageshow"));
    setVisibility("visible");
    document.dispatchEvent(new Event("visibilitychange"));
    unsubscribe();

    expect(states).toEqual(["hidden", "terminated", "visible", "visible"]);
  });

  it("unsubscribe removes all three listeners", () => {
    const states: BackgroundState[] = [];
    webBackground()!.subscribe((s) => states.push(s))();

    document.dispatchEvent(new Event("visibilitychange"));
    window.dispatchEvent(new Event("pagehide"));
    window.dispatchEvent(new Event("pageshow"));

    expect(states).toEqual([]);
  });
});

describe("webPlatform", () => {
  it("opens the stream with the given fetch and uses localStorage and the page visibility", async () => {
    const fetchFn = vi.fn(
      async () => new Response("data: x\n\n", { status: 200 }),
    );
    const platform = webPlatform(fetchFn as typeof fetch);

    const res = await platform.openStream(
      "https://flags.example.com/sdk/v1/stream",
      {},
      new AbortController().signal,
    );

    expect(fetchFn).toHaveBeenCalledOnce();
    expect(res.status).toBe(200);
    expect(platform.storage).toBe(localStorage);
    expect(platform.background).not.toBeNull();
  });

  it("is exported from the platform entry point", () => {
    expect(typeof fromPlatformEntry).toBe("function");
    expect(fromPlatformEntry).toBe(webPlatform);
  });
});

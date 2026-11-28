import { describe, expect, it } from "vitest";
import { Cache } from "../src/internal/cache.js";
import type { EvaluateResult } from "../src/internal/options.js";
import { storedFrom } from "../src/internal/store.js";
import type { Logger } from "../src/internal/transport.js";
import type { StorageLike } from "../src/platform.js";

const SDK_KEY = "mf_client_secretsecret";
const NOW = new Date("2026-11-15T10:20:30.456Z");

const result = (o: Partial<EvaluateResult> = {}): EvaluateResult => ({
  key: "f",
  value: true,
  variant: "on",
  reason: "TARGETING_MATCH",
  error_code: null,
  error_message: null,
  ...o,
});

const stored = (contextHash = "h1", etag: string | null = '"e1"') =>
  storedFrom([result(), result({ key: "g", value: { a: [1, 2] } })], {
    etag,
    source: "network",
    contextHash,
    targetingKey: "user-1",
  });

interface Call {
  op: string;
  key: string;
}

class SyncStorage implements StorageLike {
  readonly items = new Map<string, string>();
  readonly calls: Call[] = [];
  getItem(key: string): string | null {
    this.calls.push({ op: "get", key });
    return this.items.get(key) ?? null;
  }
  setItem(key: string, value: string): void {
    this.calls.push({ op: "set", key });
    this.items.set(key, value);
  }
  removeItem(key: string): void {
    this.calls.push({ op: "remove", key });
    this.items.delete(key);
  }
}

class AsyncStorage implements StorageLike {
  readonly items = new Map<string, string>();
  async getItem(key: string): Promise<string | null> {
    await Promise.resolve();
    return this.items.get(key) ?? null;
  }
  async setItem(key: string, value: string): Promise<void> {
    await Promise.resolve();
    this.items.set(key, value);
  }
  async removeItem(key: string): Promise<void> {
    await Promise.resolve();
    this.items.delete(key);
  }
}

interface Logged {
  level: keyof Logger;
  message: string;
}

const capture = () => {
  const lines: Logged[] = [];
  const logger: Logger = {
    debug: (message) => lines.push({ level: "debug", message }),
    info: (message) => lines.push({ level: "info", message }),
    warn: (message) => lines.push({ level: "warn", message }),
    error: (message) => lines.push({ level: "error", message }),
  };
  return { lines, logger };
};

const make = (storage: StorageLike | null) => {
  const log = capture();
  const cache = new Cache(SDK_KEY, storage, log.logger, () => NOW);
  return { cache, ...log };
};

describe.each([
  ["sync", () => new SyncStorage()],
  ["async", () => new AsyncStorage()],
])("Cache over %s storage", (_name, newStorage) => {
  it("round-trips results, etag and context hash with source cache", async () => {
    const storage = newStorage();
    const { cache } = make(storage);
    await cache.save(stored());
    const loaded = await cache.load("h1");
    expect(loaded).toEqual({
      results: stored().results,
      etag: '"e1"',
      source: "cache",
      contextHash: "h1",
      targetingKey: null,
    });
  });

  it("keeps a null etag", async () => {
    const { cache } = make(newStorage());
    await cache.save(stored("h1", null));
    expect((await cache.load("h1"))?.etag).toBeNull();
  });

  it("writes exactly the entry fields", async () => {
    const storage = newStorage();
    const { cache } = make(storage);
    await cache.save(stored());
    const raw = storage.items.get(cache.key);
    expect(JSON.parse(raw!)).toEqual({
      v: 1,
      context_hash: "h1",
      etag: '"e1"',
      results: [result(), result({ key: "g", value: { a: [1, 2] } })],
      saved_at: "2026-11-15T10:20:30.456Z",
    });
    expect(Object.keys(JSON.parse(raw!))).toEqual([
      "v",
      "context_hash",
      "etag",
      "results",
      "saved_at",
    ]);
  });

  it("returns undefined for another context hash", async () => {
    const { cache, lines } = make(newStorage());
    await cache.save(stored("h1"));
    expect(await cache.load("h2")).toBeUndefined();
    expect(lines.some((l) => l.level === "debug")).toBe(true);
  });

  it("returns undefined when nothing is stored", async () => {
    const { cache } = make(newStorage());
    expect(await cache.load("h1")).toBeUndefined();
  });

  it.each([
    [
      "v 2",
      JSON.stringify({
        v: 2,
        context_hash: "h1",
        etag: null,
        results: [],
        saved_at: "x",
      }),
    ],
    ["corrupt JSON", "{not json"],
    ["a non-object", "[]"],
    [
      "a bad result item",
      JSON.stringify({
        v: 1,
        context_hash: "h1",
        etag: null,
        results: [{ key: "f" }],
        saved_at: "x",
      }),
    ],
    [
      "a non-string etag",
      JSON.stringify({
        v: 1,
        context_hash: "h1",
        etag: 3,
        results: [],
        saved_at: "x",
      }),
    ],
  ])("returns undefined for %s", async (_what, raw) => {
    const storage = newStorage();
    const { cache, lines } = make(storage);
    storage.items.set(cache.key, raw);
    expect(await cache.load("h1")).toBeUndefined();
    expect(lines.filter((l) => l.level === "debug")).toHaveLength(1);
    expect(lines.filter((l) => l.level !== "debug")).toEqual([]);
  });

  it("remove deletes the entry", async () => {
    const storage = newStorage();
    const { cache } = make(storage);
    await cache.save(stored());
    await cache.remove();
    expect(storage.items.has(cache.key)).toBe(false);
    expect(await cache.load("h1")).toBeUndefined();
  });
});

describe("Cache key", () => {
  it("is megooflags- plus 16 hex and does not contain the SDK key", () => {
    const { cache } = make(new SyncStorage());
    expect(cache.key).toMatch(/^megooflags-[0-9a-f]{16}$/);
    expect(cache.key).not.toContain(SDK_KEY);
    expect(cache.key).not.toContain("secret");
  });

  it("differs per SDK key", () => {
    const a = new Cache("mf_client_a", null, capture().logger);
    const b = new Cache("mf_client_b", null, capture().logger);
    expect(a.key).not.toBe(b.key);
  });
});

describe("Cache failures", () => {
  const failing = (op: "get" | "set" | "remove", async: boolean) => {
    const storage = new SyncStorage();
    const boom = () => {
      const err = new Error(`${SDK_KEY} quota`);
      err.name = "QuotaExceededError";
      if (async) return Promise.reject(err);
      throw err;
    };
    if (op === "get") storage.getItem = boom as never;
    if (op === "set") storage.setItem = boom as never;
    if (op === "remove") storage.removeItem = boom as never;
    return storage;
  };

  it.each([false, true])(
    "save resolves and warns once when setItem fails (async %s)",
    async (async) => {
      const { cache, lines } = make(failing("set", async));
      await expect(cache.save(stored())).resolves.toBeUndefined();
      const warns = lines.filter((l) => l.level === "warn");
      expect(warns).toEqual([
        {
          level: "warn",
          message: "megooflags: cache write failed: QuotaExceededError",
        },
      ]);
      expect(warns[0]!.message).not.toContain(SDK_KEY);
      expect(warns[0]!.message).not.toContain(cache.key);
    },
  );

  it.each([false, true])(
    "remove resolves and warns when removeItem fails (async %s)",
    async (async) => {
      const { cache, lines } = make(failing("remove", async));
      await expect(cache.remove()).resolves.toBeUndefined();
      expect(lines.filter((l) => l.level === "warn")).toHaveLength(1);
    },
  );

  it.each([false, true])(
    "load resolves undefined when getItem fails (async %s)",
    async (async) => {
      const { cache, lines } = make(failing("get", async));
      await expect(cache.load("h1")).resolves.toBeUndefined();
      expect(lines.filter((l) => l.level === "error")).toEqual([]);
    },
  );
});

describe("Cache with null storage", () => {
  it("loads undefined and does nothing on save and remove", async () => {
    const { cache, lines } = make(null);
    expect(await cache.load("h1")).toBeUndefined();
    await cache.save(stored());
    await cache.remove();
    expect(lines).toEqual([]);
  });
});

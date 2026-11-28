import { describe, expect, it } from "vitest";
import type { MegooFlagsOptions } from "../src/config.js";
import { parseResults, resolveOptions } from "../src/internal/options.js";
import { defaultLogger } from "../src/internal/transport.js";

const KEY = "mf_client_abc123";
const BASE = "https://flags.example";

const opts = (o: Partial<MegooFlagsOptions> = {}): MegooFlagsOptions => ({
  sdkKey: KEY,
  baseUrl: BASE,
  ...o,
});

const result = (o: Record<string, unknown> = {}) => ({
  key: "f",
  value: true,
  variant: "on",
  reason: "TARGETING_MATCH",
  error_code: null,
  error_message: null,
  ...o,
});

describe("resolveOptions sdkKey", () => {
  it("refuses a server key without naming it", () => {
    const key = "mf_server_secret42";
    let message = "";
    try {
      resolveOptions(opts({ sdkKey: key }));
    } catch (err) {
      message = (err as Error).message;
    }
    expect(message).toBe(
      "megooflags: a server key must never ship in an app; use a client key (mf_client_…)",
    );
    expect(message).not.toContain(key);
  });

  it("refuses a key that is not a client key", () => {
    expect(() => resolveOptions(opts({ sdkKey: "random" }))).toThrow(
      "megooflags: sdkKey must be a client key (mf_client_…)",
    );
  });

  it("refuses a missing key", () => {
    expect(() =>
      resolveOptions({ baseUrl: BASE } as unknown as MegooFlagsOptions),
    ).toThrow("must be a client key");
  });

  it("accepts a client key", () => {
    expect(resolveOptions(opts()).sdkKey).toBe(KEY);
  });
});

describe("resolveOptions baseUrl", () => {
  it("requires baseUrl when not offline", () => {
    expect(() => resolveOptions({ sdkKey: KEY })).toThrow(
      "megooflags: baseUrl is required",
    );
  });

  it("refuses a URL that does not parse", () => {
    expect(() => resolveOptions(opts({ baseUrl: "not a url" }))).toThrow(
      "megooflags: baseUrl",
    );
  });

  it("refuses a scheme other than http or https", () => {
    expect(() => resolveOptions(opts({ baseUrl: "ftp://x" }))).toThrow(
      "megooflags: baseUrl",
    );
  });

  it("removes trailing slashes", () => {
    expect(
      resolveOptions(opts({ baseUrl: "https://a.example/" })).baseUrl,
    ).toBe("https://a.example");
    expect(
      resolveOptions(opts({ baseUrl: "http://a.example/api//" })).baseUrl,
    ).toBe("http://a.example/api");
  });

  it("accepts offline without baseUrl", () => {
    const r = resolveOptions({ sdkKey: KEY, offline: true });
    expect(r.offline).toBe(true);
    expect(r.baseUrl).toBe("");
  });
});

describe("resolveOptions intervals", () => {
  it("raises a short poll interval to 15 s", () => {
    expect(resolveOptions(opts({ pollIntervalMs: 1000 })).pollIntervalMs).toBe(
      15000,
    );
    expect(resolveOptions(opts({ pollIntervalMs: 20000 })).pollIntervalMs).toBe(
      20000,
    );
  });

  it("clamps the flush interval to 10…60 s", () => {
    expect(
      resolveOptions(opts({ flushIntervalMs: 1000 })).flushIntervalMs,
    ).toBe(10000);
    expect(
      resolveOptions(opts({ flushIntervalMs: 600000 })).flushIntervalMs,
    ).toBe(60000);
    expect(
      resolveOptions(opts({ flushIntervalMs: 20000 })).flushIntervalMs,
    ).toBe(20000);
  });

  it("refuses an init timeout that is not positive", () => {
    expect(() => resolveOptions(opts({ initTimeoutMs: 0 }))).toThrow(
      "megooflags: initTimeoutMs must be greater than 0",
    );
    expect(() => resolveOptions(opts({ initTimeoutMs: Number.NaN }))).toThrow(
      "initTimeoutMs",
    );
  });
});

describe("resolveOptions defaults", () => {
  it("fills every default", () => {
    const r = resolveOptions(opts());
    expect(r).toEqual({
      sdkKey: KEY,
      baseUrl: BASE,
      bootstrap: null,
      disableCache: false,
      offline: false,
      initTimeoutMs: 5000,
      disableStream: false,
      pollIntervalMs: 30000,
      flushIntervalMs: 30000,
      sendContext: false,
      logger: defaultLogger,
      fetch: null,
      storage: null,
    });
    expect(r.logger).toBe(defaultLogger);
  });

  it("keeps the given logger, fetch and storage", () => {
    const logger = { ...defaultLogger };
    const fetchFn = (() => Promise.reject(new Error("x"))) as typeof fetch;
    const storage = {
      getItem: () => null,
      setItem: () => {},
      removeItem: () => {},
    };
    const r = resolveOptions(opts({ logger, fetch: fetchFn, storage }));
    expect(r.logger).toBe(logger);
    expect(r.fetch).toBe(fetchFn);
    expect(r.storage).toBe(storage);
  });
});

describe("resolveOptions bootstrap", () => {
  it("accepts a valid body", () => {
    const r = resolveOptions(opts({ bootstrap: { results: [result()] } }));
    expect(r.bootstrap).toEqual([result()]);
  });

  it("names the invalid field", () => {
    expect(() =>
      resolveOptions(opts({ bootstrap: { results: [{ key: 1 }] } })),
    ).toThrow("megooflags: invalid bootstrap: results[0].key");
  });

  it("refuses a body that is not an object", () => {
    expect(() =>
      resolveOptions(
        opts({ bootstrap: "x" as unknown as { results: unknown[] } }),
      ),
    ).toThrow("megooflags: invalid bootstrap");
  });

  it("refuses a duplicate key", () => {
    expect(() =>
      resolveOptions(
        opts({ bootstrap: { results: [result(), result({ value: false })] } }),
      ),
    ).toThrow("megooflags: invalid bootstrap: results[1].key");
  });
});

describe("parseResults", () => {
  it("turns a missing error_message into null and drops extra fields", () => {
    const raw = { ...result({ extra: 1 }) } as Record<string, unknown>;
    delete raw.error_message;
    expect(parseResults({ results: [raw] })).toEqual([result()]);
  });

  it("keeps an error result", () => {
    const r = result({
      value: null,
      variant: null,
      reason: "ERROR",
      error_code: "FLAG_NOT_FOUND",
      error_message: "no such flag",
    });
    expect(parseResults({ results: [r] })).toEqual([r]);
  });

  it.each([
    ["null", null, "invalid results: not an object"],
    ["an array", [], "invalid results: not an object"],
    ["no results", {}, "invalid results: results: not an array"],
    [
      "an item that is not an object",
      { results: [1] },
      "results[0]: not an object",
    ],
    [
      "a missing value",
      { results: [{ ...result(), value: undefined }] },
      "results[0].value: missing",
    ],
    [
      "a numeric variant",
      { results: [result({ variant: 1 })] },
      "results[0].variant: not a string or null",
    ],
    [
      "a missing reason",
      { results: [result({ reason: undefined })] },
      "results[0].reason: not a string",
    ],
    [
      "a numeric error_code",
      { results: [result({ error_code: 3 })] },
      "results[0].error_code: not a string or null",
    ],
    [
      "a numeric error_message",
      { results: [result({ error_message: 3 })] },
      "results[0].error_message: not a string or null",
    ],
  ])("refuses %s", (_name, raw, message) => {
    expect(() => parseResults(raw, "invalid results")).toThrow(message);
  });
});

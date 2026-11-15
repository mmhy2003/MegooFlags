import { afterEach, describe, expect, it, vi } from "vitest";
import {
  buildHeaders,
  defaultLogger,
  FatalError,
  HttpError,
  parseRetryAfter,
  RetryAfterError,
  sdkUrl,
  statusError,
} from "../src/internal/transport.js";

const KEY = "mf_client_x";
const NOW = Date.UTC(2026, 10, 14, 12, 0, 0);

const header =
  (h: Record<string, string> = {}) =>
  (name: string): string | null =>
    new Headers(h).get(name);

describe("buildHeaders", () => {
  it("sends the key as a bearer token, without a User-Agent", () => {
    expect(buildHeaders({ sdkKey: KEY, accept: "application/json" })).toEqual({
      Authorization: "Bearer mf_client_x",
      Accept: "application/json",
    });
  });

  it("adds Content-Type only for a JSON body", () => {
    expect(
      buildHeaders({ sdkKey: KEY, accept: "application/json", json: true })[
        "Content-Type"
      ],
    ).toBe("application/json");
    expect(
      buildHeaders({ sdkKey: KEY, accept: "application/json" }),
    ).not.toHaveProperty("Content-Type");
  });

  it("adds If-None-Match only when an ETag is given", () => {
    expect(
      buildHeaders({ sdkKey: KEY, accept: "application/json", etag: '"e1"' })[
        "If-None-Match"
      ],
    ).toBe('"e1"');
    expect(
      buildHeaders({ sdkKey: KEY, accept: "application/json", etag: null }),
    ).not.toHaveProperty("If-None-Match");
    expect(
      buildHeaders({ sdkKey: KEY, accept: "application/json" }),
    ).not.toHaveProperty("If-None-Match");
  });

  it("adds Last-Event-ID only when one is given", () => {
    const h = buildHeaders({
      sdkKey: KEY,
      accept: "text/event-stream",
      lastEventId: "42",
    });
    expect(h.Accept).toBe("text/event-stream");
    expect(h["Last-Event-ID"]).toBe("42");
    expect(
      buildHeaders({ sdkKey: KEY, accept: "text/event-stream" }),
    ).not.toHaveProperty("Last-Event-ID");
  });

  it("never sends a User-Agent", () => {
    const h = buildHeaders({
      sdkKey: KEY,
      accept: "text/event-stream",
      etag: "e",
      lastEventId: "1",
      json: true,
    });
    expect(Object.keys(h).map((k) => k.toLowerCase())).not.toContain(
      "user-agent",
    );
  });
});

describe("sdkUrl", () => {
  it.each([
    ["https://flags.example.com", "evaluate"],
    ["https://flags.example.com/", "stream"],
    ["https://flags.example.com/base//", "events"],
  ] as const)("%s %s", (base, path) => {
    const url = sdkUrl(base, path);
    expect(url).toBe(`${base.replace(/\/+$/, "")}/sdk/v1/${path}`);
    expect(url).not.toContain(KEY);
  });
});

describe("statusError", () => {
  it.each([200, 202, 204, 304])("%d is not an error", (status) => {
    expect(statusError(status, header(), NOW)).toBeUndefined();
  });

  it.each([401, 403])("%d is fatal", (status) => {
    const err = statusError(status, header(), NOW);
    expect(err).toBeInstanceOf(FatalError);
    expect((err as FatalError).status).toBe(status);
    expect(err?.message).toBe(`megooflags: SDK key refused (${status})`);
  });

  it("429 carries integer Retry-After seconds", () => {
    const err = statusError(429, header({ "Retry-After": "7" }), NOW);
    expect(err).toBeInstanceOf(RetryAfterError);
    expect((err as RetryAfterError).seconds).toBe(7);
    expect(err?.message).toBe("megooflags: rate limited, retry after 7s");
  });

  it("429 reads an HTTP-date Retry-After", () => {
    const at = new Date(NOW + 30_000).toUTCString();
    const err = statusError(429, header({ "Retry-After": at }), NOW);
    expect((err as RetryAfterError).seconds).toBe(30);
  });

  it("429 without Retry-After waits 60 s", () => {
    const err = statusError(429, header(), NOW);
    expect(err).toBeInstanceOf(RetryAfterError);
    expect((err as RetryAfterError).seconds).toBe(60);
  });

  it("uses the current time by default", () => {
    vi.useFakeTimers({ now: NOW });
    try {
      const at = new Date(NOW + 5000).toUTCString();
      const err = statusError(429, header({ "Retry-After": at }));
      expect((err as RetryAfterError).seconds).toBe(5);
    } finally {
      vi.useRealTimers();
    }
  });

  it.each([404, 413, 422, 500, 503])("%d is an HTTP error", (status) => {
    const err = statusError(status, header(), NOW);
    expect(err).toBeInstanceOf(HttpError);
    expect((err as HttpError).status).toBe(status);
    expect(err?.message).toBe(`megooflags: HTTP ${status}`);
  });

  it("revoked is fatal", () => {
    const err = new FatalError("revoked");
    expect(err.message).toBe("megooflags: SDK key refused (revoked)");
    expect(err.status).toBe("revoked");
  });

  it("never puts the key in a message", () => {
    for (const err of [
      new FatalError(401),
      new FatalError(403),
      new FatalError("revoked"),
      statusError(429, header(), NOW),
      statusError(500, header(), NOW),
    ]) {
      expect(err?.message).not.toContain(KEY);
      expect(err?.message).not.toContain("mf_client_");
    }
  });
});

describe("parseRetryAfter", () => {
  const httpDate = (ms: number) => new Date(ms).toUTCString();

  it.each([
    ["5", 5],
    ["0", 0],
    ["-1", 0],
    [" 7 ", 7],
    [httpDate(NOW + 30_000), 30],
    [httpDate(NOW - 3_600_000), 0],
    ["Sunday, 14-Nov-26 12:00:30 GMT", 30],
    ["Sat Nov 14 12:00:30 2026", 30],
  ])("%j is %d s", (value, want) => {
    expect(parseRetryAfter(value, NOW)).toBe(want);
  });

  it("rounds a partial second of an HTTP date up", () => {
    expect(parseRetryAfter(httpDate(NOW + 30_000), NOW - 500)).toBe(31);
  });

  it.each([
    "abc",
    "",
    "1.5",
    "5s",
    "Sat, 14 Nov 2026 12:00:30",
    "Sat, 31 Feb 2026 12:00:30 GMT",
    null,
    undefined,
  ])("%j is 60 s", (value) => {
    expect(parseRetryAfter(value, NOW)).toBe(60);
  });
});

describe("defaultLogger", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("writes warn and error to the console with the prefix", () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    const error = vi.spyOn(console, "error").mockImplementation(() => {});
    defaultLogger.warn("stream failed: x");
    defaultLogger.error("megooflags: boom");
    expect(warn).toHaveBeenCalledWith("megooflags: stream failed: x");
    expect(error).toHaveBeenCalledWith("megooflags: boom");
  });

  it("keeps debug and info silent", () => {
    const spies = (["debug", "info", "log"] as const).map((m) =>
      vi.spyOn(console, m).mockImplementation(() => {}),
    );
    defaultLogger.debug("megooflags: stream connected");
    defaultLogger.info("megooflags: hello");
    for (const s of spies) {
      expect(s).not.toHaveBeenCalled();
    }
  });
});

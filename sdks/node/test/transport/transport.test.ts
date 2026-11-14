import { describe, expect, it } from "vitest";
import {
  buildHeaders,
  FatalError,
  HttpError,
  parseRetryAfter,
  RetryAfterError,
  sdkUrl,
  statusError,
  USER_AGENT,
} from "../../src/internal/transport/transport.js";
import { VERSION } from "../../src/version.js";

const KEY = "mf_server_secret-key-123";
const NOW = Date.UTC(2026, 10, 14, 12, 0, 0);

describe("buildHeaders", () => {
  it("sends the key as a bearer token with the user agent", () => {
    expect(buildHeaders(KEY)).toEqual({
      Authorization: `Bearer ${KEY}`,
      "User-Agent": `megooflags-node/${VERSION}`,
      Accept: "application/json",
    });
    expect(USER_AGENT).toBe(`megooflags-node/${VERSION}`);
  });

  it("adds Content-Type only with a body", () => {
    expect(buildHeaders(KEY, { body: true })["Content-Type"]).toBe(
      "application/json",
    );
    expect(buildHeaders(KEY)).not.toHaveProperty("Content-Type");
  });

  it("asks for an event stream without caching", () => {
    const h = buildHeaders(KEY, { stream: true });
    expect(h.Accept).toBe("text/event-stream");
    expect(h["Cache-Control"]).toBe("no-cache");
    expect(h).not.toHaveProperty("Content-Type");
  });
});

describe("sdkUrl", () => {
  it.each([
    ["https://flags.example.com", "ruleset"],
    ["https://flags.example.com/", "stream"],
    ["https://flags.example.com/base//", "events"],
  ] as const)("%s %s", (base, endpoint) => {
    const url = sdkUrl(base, endpoint);
    expect(url).toBe(`${base.replace(/\/+$/, "")}/sdk/v1/${endpoint}`);
    expect(url).not.toContain(KEY);
  });
});

describe("statusError", () => {
  const headers = (h: Record<string, string> = {}) => new Headers(h);

  it.each([200, 204, 304])("%d is not an error", (status) => {
    expect(statusError(status, headers(), NOW)).toBeUndefined();
  });

  it.each([401, 403])("%d is fatal", (status) => {
    const err = statusError(status, headers(), NOW);
    expect(err).toBeInstanceOf(FatalError);
    expect((err as FatalError).status).toBe(status);
    expect(err?.message).toBe(`megooflags: SDK key refused (${status})`);
  });

  it("429 carries Retry-After", () => {
    const err = statusError(429, headers({ "Retry-After": "7" }), NOW);
    expect(err).toBeInstanceOf(RetryAfterError);
    expect((err as RetryAfterError).afterMs).toBe(7000);
    expect(err?.message).toBe("megooflags: rate limited, retry after 7s");
  });

  it("429 without Retry-After waits 0", () => {
    const err = statusError(429, headers(), NOW);
    expect(err).toBeInstanceOf(RetryAfterError);
    expect((err as RetryAfterError).afterMs).toBe(0);
  });

  it.each([404, 413, 422, 500, 503])("%d is an HTTP error", (status) => {
    const err = statusError(status, headers(), NOW);
    expect(err).toBeInstanceOf(HttpError);
    expect((err as HttpError).status).toBe(status);
    expect(err?.message).toBe(`megooflags: HTTP ${status}`);
  });

  it("revoked is fatal", () => {
    const err = new FatalError(undefined, "revoked");
    expect(err.message).toBe("megooflags: SDK key refused (revoked)");
    expect(err.status).toBeUndefined();
    expect(err.reason).toBe("revoked");
  });
});

describe("parseRetryAfter", () => {
  const httpDate = (ms: number) => new Date(ms).toUTCString();

  it.each([
    ["5", 5000],
    ["0", 0],
    ["-1", 0],
    [" 7 ", 7000],
    [httpDate(NOW + 30_000), 30_000],
    [httpDate(NOW - 3_600_000), 0],
    ["Sunday, 14-Nov-26 12:00:30 GMT", 30_000],
    ["Sat Nov 14 12:00:30 2026", 30_000],
  ])("%j is %d ms", (value, want) => {
    expect(parseRetryAfter(value, NOW)).toBe(want);
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
  ])("%j is none", (value) => {
    expect(parseRetryAfter(value, NOW)).toBeUndefined();
  });
});

import { describe, expect, it } from "vitest";
import {
  validateConfig,
  withDefaults,
  type MegooFlagsConfig,
} from "../src/config.js";

const KEY = "mf_server_config-secret-value";

function check(cfg: MegooFlagsConfig): string | undefined {
  return validateConfig(withDefaults(cfg))?.message;
}

describe("withDefaults", () => {
  it("fills in the defaults", () => {
    const c = withDefaults({ sdkKey: KEY, baseUrl: "https://flags.test" });
    expect(c.initTimeoutMs).toBe(5_000);
    expect(c.pollIntervalMs).toBe(30_000);
    expect(c.flushIntervalMs).toBe(30_000);
    expect(c.offline).toBe(false);
    expect(c.disableStream).toBe(false);
    expect(c.sendContext).toBe(false);
    expect(c.logger).toBe(console);
    expect(c.fetch).toBe(globalThis.fetch);
  });

  it("clamps the intervals", () => {
    const c = (pollIntervalMs: number, flushIntervalMs: number) =>
      withDefaults({ sdkKey: KEY, pollIntervalMs, flushIntervalMs });
    expect(c(1_000, 1_000).pollIntervalMs).toBe(15_000);
    expect(c(1_000, 1_000).flushIntervalMs).toBe(10_000);
    expect(c(45_000, 45_000).pollIntervalMs).toBe(45_000);
    expect(c(45_000, 45_000).flushIntervalMs).toBe(45_000);
    expect(c(600_000, 600_000).pollIntervalMs).toBe(600_000);
    expect(c(600_000, 600_000).flushIntervalMs).toBe(60_000);
  });

  it("treats zero, negative and NaN durations as unset", () => {
    for (const ms of [0, -1, Number.NaN]) {
      const c = withDefaults({
        sdkKey: KEY,
        initTimeoutMs: ms,
        pollIntervalMs: ms,
        flushIntervalMs: ms,
      });
      expect(c.initTimeoutMs).toBe(5_000);
      expect(c.pollIntervalMs).toBe(30_000);
      expect(c.flushIntervalMs).toBe(30_000);
    }
  });

  it("removes trailing slashes from the base URL", () => {
    expect(withDefaults({ sdkKey: KEY, baseUrl: "http://h:1//" }).baseUrl).toBe(
      "http://h:1",
    );
  });
});

describe("validateConfig", () => {
  it("accepts a server key with an http or https base URL", () => {
    expect(check({ sdkKey: KEY, baseUrl: "https://flags.test" })).toBe(
      undefined,
    );
    expect(check({ sdkKey: KEY, baseUrl: "http://127.0.0.1:8080/" })).toBe(
      undefined,
    );
  });

  it("refuses a client key", () => {
    expect(
      check({ sdkKey: "mf_client_abc", baseUrl: "https://flags.test" }),
    ).toBe(
      "megooflags: a client SDK key cannot be used by a server SDK; use a server key (mf_server_…)",
    );
  });

  it("refuses a key that is not a server key", () => {
    for (const sdkKey of ["", "abc", "mf_serverx"]) {
      expect(check({ sdkKey, baseUrl: "https://flags.test" })).toBe(
        "megooflags: invalid SDK key; use a server key (mf_server_…)",
      );
    }
  });

  it("refuses a base URL that is not http or https with a host", () => {
    for (const baseUrl of [
      undefined,
      "",
      "flags.test",
      "ftp://flags.test",
      "file:///tmp/x",
      "http://",
    ]) {
      expect(
        check({ sdkKey: KEY, ...(baseUrl === undefined ? {} : { baseUrl }) }),
        String(baseUrl),
      ).toBe("megooflags: baseUrl must be an http or https URL");
    }
  });

  it("needs a bootstrap file or a cache directory offline", () => {
    expect(check({ sdkKey: KEY, offline: true })).toBe(
      "megooflags: offline needs a bootstrapFile or a cacheDir",
    );
    expect(check({ sdkKey: KEY, offline: true, cacheDir: "/tmp/x" })).toBe(
      undefined,
    );
    expect(
      check({ sdkKey: KEY, offline: true, bootstrapFile: "/tmp/r.json" }),
    ).toBe(undefined);
  });

  it("never puts the key in a message", () => {
    const keys = [KEY, "mf_client_client-secret-value", "not-a-key-secret"];
    for (const sdkKey of keys) {
      for (const cfg of [
        { sdkKey },
        { sdkKey, baseUrl: "nope" },
        { sdkKey, offline: true },
      ]) {
        const msg = check(cfg) ?? "";
        expect(msg).not.toContain(sdkKey);
        expect(msg).not.toContain("secret");
      }
    }
  });
});

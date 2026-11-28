import { describe, expect, it } from "vitest";

import { shouldWarnPlainHttp } from "@/lib/http-warning";

describe("shouldWarnPlainHttp", () => {
  it("does not warn over https", () => {
    expect(
      shouldWarnPlainHttp({
        forwardedProto: "https",
        host: "flags.example.com",
      }),
    ).toBe(false);
  });

  it("treats a missing forwarded proto as http", () => {
    expect(
      shouldWarnPlainHttp({
        forwardedProto: null,
        host: "flags.example.com:8080",
      }),
    ).toBe(true);
  });

  it.each(["localhost:3000", "127.0.0.1", "[::1]:3000"])(
    "does not warn for loopback host %s",
    (host) => {
      expect(shouldWarnPlainHttp({ forwardedProto: "http", host })).toBe(false);
    },
  );

  it("warns for other 127.x addresses", () => {
    expect(
      shouldWarnPlainHttp({ forwardedProto: null, host: "127.0.0.2:3000" }),
    ).toBe(true);
  });

  it("uses the first forwarded proto, trimmed and lower-cased", () => {
    expect(
      shouldWarnPlainHttp({ forwardedProto: "HTTPS, http", host: "x.com" }),
    ).toBe(false);
  });

  it("does not warn without a host", () => {
    expect(shouldWarnPlainHttp({ forwardedProto: "http", host: null })).toBe(
      false,
    );
  });
});

import { describe, expect, it } from "vitest";
import {
  canonicalJson,
  contextHash,
  targetingKeyOf,
  toRequestContext,
} from "../src/internal/context.js";

describe("toRequestContext", () => {
  it("writes a non-empty targeting key as key", () => {
    expect(toRequestContext({ targetingKey: "u1", plan: "pro" })).toEqual({
      key: "u1",
      plan: "pro",
    });
  });

  it("replaces a key attribute with the targeting key", () => {
    expect(toRequestContext({ targetingKey: "u1", key: "other" })).toEqual({
      key: "u1",
    });
  });

  it("keeps a key attribute without a targeting key", () => {
    expect(toRequestContext({ key: "u2" })).toEqual({ key: "u2" });
  });

  it("drops an empty targeting key", () => {
    expect(toRequestContext({ targetingKey: "", plan: "free" })).toEqual({
      plan: "free",
    });
  });

  it("writes dates as UTC with milliseconds", () => {
    expect(
      toRequestContext({
        at: new Date("2026-11-10T11:15:02+02:00"),
        nested: { list: [new Date("2026-11-10T09:15:02.5Z")] },
      }),
    ).toEqual({
      at: "2026-11-10T09:15:02.000Z",
      nested: { list: ["2026-11-10T09:15:02.500Z"] },
    });
  });

  it("copies nested structures and drops undefined", () => {
    const ctx = {
      device: { os: "ios", tags: ["beta", { level: 2 }], gone: undefined },
      gone: undefined,
      team: null,
    } as unknown as Parameters<typeof toRequestContext>[0];
    const body = toRequestContext(ctx);
    expect(body).toStrictEqual({
      device: { os: "ios", tags: ["beta", { level: 2 }] },
      team: null,
    });
    expect(body.device).not.toBe(ctx.device);
  });
});

describe("canonicalJson", () => {
  it("sorts keys by code point, not UTF-16 unit", () => {
    expect(canonicalJson({ "😀": 1, "～": 2, é: 3, Z: 4, a: 5 })).toBe(
      '{"Z":4,"a":5,"é":3,"～":2,"😀":1}',
    );
  });

  it("sorts nested keys and has no whitespace", () => {
    expect(canonicalJson({ b: [{ y: 1, x: 2 }, []], a: {} })).toBe(
      '{"a":{},"b":[{"x":2,"y":1},[]]}',
    );
  });

  it("escapes only quote, backslash and control characters", () => {
    expect(
      canonicalJson('q " b \\ n \n t \t \b\f\r \u0001 \u001f café 😀'),
    ).toBe('"q \\" b \\\\ n \\n t \\t \\b\\f\\r \\u0001 \\u001f café 😀"');
  });

  it("writes integral numbers as integers", () => {
    expect(canonicalJson([3.0, -7, 0, 1.5, 1e21, 2 ** 52])).toBe(
      "[3,-7,0,1.5,1e+21,4503599627370496]",
    );
  });

  it("writes booleans and null", () => {
    expect(canonicalJson([true, false, null])).toBe("[true,false,null]");
  });
});

describe("contextHash", () => {
  it("is the SHA-256 of the canonical JSON", () => {
    expect(contextHash({ plan: "pro", key: "user-1" })).toBe(
      "e38a15b496693e013a31fba1d1c8804c3c06f1d0686e0152549c8c05609babbe",
    );
  });
});

describe("targetingKeyOf", () => {
  it("is key when it is a string", () => {
    expect(targetingKeyOf({ key: "u1" })).toBe("u1");
  });

  it.each([[{}], [{ key: 1 }], [{ key: null }]])("is null for %j", (body) => {
    expect(targetingKeyOf(body)).toBeNull();
  });
});

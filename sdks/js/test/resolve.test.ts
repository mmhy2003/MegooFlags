import {
  ErrorCode,
  StandardResolutionReasons,
  type JsonValue,
} from "@openfeature/web-sdk";
import { describe, expect, it } from "vitest";
import type { EvaluateResult } from "../src/internal/options.js";
import { resolveFlag, type FlagType } from "../src/internal/resolve.js";
import {
  changedFlags,
  sameJson,
  Store,
  storedFrom,
  type Source,
  type StoredResults,
} from "../src/internal/store.js";

const result = (o: Partial<EvaluateResult> = {}): EvaluateResult => ({
  key: "f",
  value: true,
  variant: "on",
  reason: "TARGETING_MATCH",
  error_code: null,
  error_message: null,
  ...o,
});

const stored = (
  results: EvaluateResult[],
  source: Source = "network",
): StoredResults =>
  storedFrom(results, {
    etag: null,
    source,
    contextHash: "h",
    targetingKey: null,
  });

describe("Store", () => {
  it("starts empty, holds one StoredResults and can be cleared", () => {
    const store = new Store();
    expect(store.get()).toBeUndefined();
    const s = stored([result()]);
    store.set(s);
    expect(store.get()).toBe(s);
    store.set(undefined);
    expect(store.get()).toBeUndefined();
  });

  it("storedFrom maps results by key and keeps the metadata", () => {
    const s = storedFrom([result({ key: "a" }), result({ key: "b" })], {
      etag: '"e1"',
      source: "cache",
      contextHash: "abc",
      targetingKey: "user-1",
    });
    expect([...s.results.keys()]).toEqual(["a", "b"]);
    expect(s.results.get("b")).toEqual(result({ key: "b" }));
    expect(s.etag).toBe('"e1"');
    expect(s.source).toBe("cache");
    expect(s.contextHash).toBe("abc");
    expect(s.targetingKey).toBe("user-1");
  });
});

describe("resolveFlag", () => {
  it("is PROVIDER_NOT_READY without stored results", () => {
    expect(resolveFlag(undefined, "f", "boolean", false)).toEqual({
      value: false,
      reason: StandardResolutionReasons.ERROR,
      errorCode: ErrorCode.PROVIDER_NOT_READY,
    });
  });

  it("is FLAG_NOT_FOUND for a missing flag", () => {
    expect(resolveFlag(stored([]), "f", "boolean", false)).toEqual({
      value: false,
      reason: StandardResolutionReasons.ERROR,
      errorCode: ErrorCode.FLAG_NOT_FOUND,
    });
  });

  it.each([
    "FLAG_NOT_FOUND",
    "PARSE_ERROR",
    "TYPE_MISMATCH",
    "TARGETING_KEY_MISSING",
    "INVALID_CONTEXT",
    "GENERAL",
  ])("maps a stored %s by name with its message", (code) => {
    const s = stored([
      result({
        value: "x",
        variant: null,
        reason: "ERROR",
        error_code: code,
        error_message: "boom",
      }),
    ]);
    expect(resolveFlag(s, "f", "string", "d")).toEqual({
      value: "d",
      reason: StandardResolutionReasons.ERROR,
      errorCode: code,
      errorMessage: "boom",
    });
  });

  it.each(["PROVIDER_NOT_READY", "PROVIDER_FATAL", "SOMETHING_NEW"])(
    "maps any other stored code (%s) to GENERAL",
    (code) => {
      const s = stored([
        result({ variant: null, reason: "ERROR", error_code: code }),
      ]);
      expect(resolveFlag(s, "f", "boolean", false)).toEqual({
        value: false,
        reason: StandardResolutionReasons.ERROR,
        errorCode: ErrorCode.GENERAL,
      });
    },
  );

  it("returns the default and the stored reason for a null variant", () => {
    const s = stored([
      result({ value: "x", variant: null, reason: "DISABLED" }),
    ]);
    expect(resolveFlag(s, "f", "string", "d")).toEqual({
      value: "d",
      reason: "DISABLED",
    });
  });

  it.each([
    ["boolean", "true", false],
    ["string", 1, "d"],
    ["number", true, 0],
    ["number", "1", 0],
    ["object", "x", {}],
    ["object", null, {}],
    ["object", 1, {}],
  ] as const)("a %s read of %j is TYPE_MISMATCH", (type, value, def) => {
    const s = stored([result({ value })]);
    expect(resolveFlag(s, "f", type, def)).toEqual({
      value: def,
      reason: StandardResolutionReasons.ERROR,
      errorCode: ErrorCode.TYPE_MISMATCH,
    });
  });

  it.each<[FlagType, JsonValue]>([
    ["boolean", false],
    ["string", ""],
    ["number", 3],
    ["number", 2.5],
    ["object", { a: [1] }],
    ["object", [1, "two"]],
    ["object", []],
  ])("serves a %s read of %j", (type, value) => {
    const s = stored([result({ value, variant: "v" })]);
    expect(resolveFlag(s, "f", type, value)).toEqual({
      value,
      variant: "v",
      reason: "TARGETING_MATCH",
    });
  });

  it.each([
    ["network", "TARGETING_MATCH"],
    ["bootstrap", "TARGETING_MATCH"],
    ["cache", StandardResolutionReasons.CACHED],
  ] as const)("from %s the reason is %s", (source, reason) => {
    const s = stored([result()], source);
    expect(resolveFlag(s, "f", "boolean", false).reason).toBe(reason);
  });
});

describe("changedFlags", () => {
  const s = (...results: EvaluateResult[]) => stored(results);

  it("is empty for equal results, deep-equal objects included", () => {
    expect(
      changedFlags(
        s(result({ key: "a", value: { x: [1, { y: 2 }] } }), result()),
        s(result(), result({ key: "a", value: { x: [1, { y: 2 }] } })),
      ),
    ).toEqual([]);
  });

  it("lists a value change", () => {
    expect(changedFlags(s(result()), s(result({ value: false })))).toEqual([
      "f",
    ]);
  });

  it("lists a variant change", () => {
    expect(changedFlags(s(result()), s(result({ variant: "other" })))).toEqual([
      "f",
    ]);
  });

  it("lists added and removed keys, sorted", () => {
    expect(
      changedFlags(
        s(result({ key: "z" }), result({ key: "same" })),
        s(result({ key: "same" }), result({ key: "b" })),
      ),
    ).toEqual(["b", "z"]);
  });
});

describe("sameJson", () => {
  it.each([
    [1, 1.0, true],
    [true, 1, false],
    ["1", 1, false],
    [null, null, true],
    [{}, [], false],
    [{ a: 1 }, { a: 1, b: 2 }, false],
    [{ a: [1, 2] }, { a: [1, 2] }, true],
  ])("sameJson(%j, %j) is %s", (a, b, want) => {
    expect(sameJson(a, b)).toBe(want);
  });
});

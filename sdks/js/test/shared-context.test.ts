// The language-neutral context cases every client SDK must pass
// (spec/client/README.md).
import { mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { basename, join } from "node:path";
import { fileURLToPath } from "node:url";
import type { EvaluationContext } from "@openfeature/web-sdk";
import { describe, expect, it } from "vitest";
import {
  canonicalJson,
  contextHash,
  toRequestContext,
} from "../src/internal/context.js";

const CLIENT_DIR = fileURLToPath(
  new URL("../../../spec/client", import.meta.url),
);

interface ContextCase {
  name: string;
  context: Record<string, unknown>;
  body: unknown;
  canonical: string;
  context_hash: string;
}

interface CaseFile {
  schema_version: unknown;
  tests: ContextCase[];
}

/** loadCaseFile refuses any schema_version other than 1. */
function loadCaseFile(path: string): CaseFile {
  const f = JSON.parse(readFileSync(path, "utf8")) as CaseFile;
  if (f.schema_version !== 1) {
    throw new Error(
      `${basename(path)}: unsupported schema_version ${JSON.stringify(f.schema_version)}`,
    );
  }
  return f;
}

/** fromCaseJson turns every {"$date": s} into new Date(s). */
function fromCaseJson(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(fromCaseJson);
  if (value !== null && typeof value === "object") {
    const o = value as Record<string, unknown>;
    const keys = Object.keys(o);
    if (keys.length === 1 && keys[0] === "$date") {
      return new Date(o.$date as string);
    }
    return Object.fromEntries(keys.map((k) => [k, fromCaseJson(o[k])]));
  }
  return value;
}

/**
 * sameJson is JSON equality: 1 == 1.0, but true != 1 and "1" != 1; arrays
 * element by element, objects key by key, {} is not [].
 */
function sameJson(a: unknown, b: unknown): boolean {
  if (a === null || b === null) return a === b;
  if (Array.isArray(a) || Array.isArray(b)) {
    return (
      Array.isArray(a) &&
      Array.isArray(b) &&
      a.length === b.length &&
      a.every((x, i) => sameJson(x, b[i]))
    );
  }
  if (typeof a === "object" && typeof b === "object") {
    const ao = a as Record<string, unknown>;
    const bo = b as Record<string, unknown>;
    const keys = Object.keys(ao);
    return (
      keys.length === Object.keys(bo).length &&
      keys.every(
        (k) =>
          Object.prototype.hasOwnProperty.call(bo, k) && sameJson(ao[k], bo[k]),
      )
    );
  }
  return typeof a === typeof b && a === b;
}

describe("shared context cases", () => {
  const file = loadCaseFile(join(CLIENT_DIR, "context.json"));

  it("has cases", () => {
    expect(file.tests.length).toBeGreaterThan(0);
  });

  for (const c of file.tests) {
    it(`context::${c.name}`, () => {
      const ctx = fromCaseJson(c.context) as EvaluationContext;
      const body = toRequestContext(ctx);
      expect(
        sameJson(body, c.body),
        `body ${JSON.stringify(body)} != ${JSON.stringify(c.body)}`,
      ).toBe(true);
      expect(canonicalJson(body)).toBe(c.canonical);
      expect(contextHash(body)).toBe(c.context_hash);
    });
  }
});

describe("shared context runner", () => {
  it("refuses a case file with schema_version 2", () => {
    const path = join(mkdtempSync(join(tmpdir(), "megooflags-")), "case.json");
    writeFileSync(
      path,
      '{"schema_version": 2, "kind": "context", "tests": []}',
    );
    expect(() => loadCaseFile(path)).toThrow("unsupported schema_version 2");
  });

  it("builds dates from $date", () => {
    expect(fromCaseJson({ a: [{ $date: "2026-11-10T09:15:02Z" }] })).toEqual({
      a: [new Date("2026-11-10T09:15:02Z")],
    });
  });

  it.each([
    [1, 1.0, true],
    [true, 1, false],
    ["1", 1, false],
    [{}, [], false],
    [{ a: 1 }, { a: 1, b: 2 }, false],
  ])("sameJson(%j, %j) is %s", (a, b, want) => {
    expect(sameJson(a, b)).toBe(want);
  });
});

// The language-neutral conformance suite every SDK must pass
// (spec/conformance/README.md).
import { mkdtempSync, readdirSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { basename, join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { bucket } from "../src/internal/engine/bucketing.js";
import {
  evaluate,
  metadata,
  type ExpectedType,
} from "../src/internal/engine/evaluate.js";
import type { Ruleset } from "../src/internal/engine/model.js";
import { parseRuleset } from "../src/internal/engine/parse.js";

const CONFORMANCE_DIR = fileURLToPath(
  new URL("../../../spec/conformance", import.meta.url),
);

interface ConformCase {
  name: string;
  flag: string;
  context: unknown;
  default: unknown;
  expected_type?: ExpectedType;
  expected: {
    value: unknown;
    variant: string | null;
    reason: string;
    error_code: string | null;
    metadata: Record<string, unknown>;
  };
}

interface CaseFile {
  schema_version: unknown;
  ruleset?: unknown;
  tests?: ConformCase[];
  vectors?: { input: string; bucket: number }[];
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
      keys.every((k) => Object.hasOwn(bo, k) && sameJson(ao[k], bo[k]))
    );
  }
  return typeof a === typeof b && a === b;
}

const caseFiles = readdirSync(join(CONFORMANCE_DIR, "cases"))
  .filter((name) => name.endsWith(".json"))
  .sort()
  .map((name) => join(CONFORMANCE_DIR, "cases", name));

describe("conformance", () => {
  it("has case files", () => {
    expect(caseFiles.length).toBeGreaterThan(0);
  });

  for (const path of caseFiles) {
    const stem = basename(path, ".json");
    const f = loadCaseFile(path);
    let rs: Ruleset | undefined;
    let parseError: unknown;
    try {
      rs = parseRuleset(f.ruleset);
    } catch (err) {
      parseError = err;
    }
    describe(stem, () => {
      for (const c of f.tests ?? []) {
        it(`${stem}::${c.name}`, () => {
          if (rs === undefined) throw parseError;
          const r = evaluate(rs, c.flag, c.context, c.default, c.expected_type);
          const want = c.expected;
          expect(sameJson(r.value, want.value), "value").toBe(true);
          expect(r.variant, "variant").toBe(want.variant);
          expect(r.reason, "reason").toBe(want.reason);
          expect(r.errorCode, "error_code").toBe(want.error_code);
          const meta = metadata(r) as Record<string, unknown>;
          for (const [k, v] of Object.entries(want.metadata)) {
            expect(Object.hasOwn(meta, k), `metadata ${k}`).toBe(true);
            expect(sameJson(meta[k], v), `metadata ${k}`).toBe(true);
          }
        });
      }
    });
  }
});

describe("bucketing", () => {
  const vectors = loadCaseFile(join(CONFORMANCE_DIR, "bucketing.json")).vectors;

  it("has at least 20 vectors", () => {
    expect(vectors?.length).toBeGreaterThanOrEqual(20);
  });

  for (const v of vectors ?? []) {
    it(`bucketing::${v.input}`, () => {
      const dot = v.input.indexOf(".");
      expect(bucket(v.input.slice(0, dot), v.input.slice(dot + 1))).toBe(
        v.bucket,
      );
    });
  }
});

describe("conformance runner", () => {
  it("refuses a case file with schema_version 2", () => {
    const path = join(mkdtempSync(join(tmpdir(), "megooflags-")), "case.json");
    writeFileSync(
      path,
      '{"schema_version": 2, "description": "", "ruleset": {}, "tests": []}',
    );
    expect(() => loadCaseFile(path)).toThrow("unsupported schema_version 2");
  });

  it.each([
    [1, 1.0, true],
    [true, 1, false],
    ["1", 1, false],
    [null, null, true],
    [null, false, false],
    [[1, { a: true }], [1.0, { a: true }], true],
    [[1], [1, 2], false],
    [{ a: 1 }, { a: 1, b: 2 }, false],
    [{ a: 0 }, { a: false }, false],
  ])("sameJson(%j, %j) is %s", (a, b, equal) => {
    expect(sameJson(a, b)).toBe(equal);
  });

  it("does not take {} for []", () => {
    expect(sameJson({}, [])).toBe(false);
    expect(sameJson([], {})).toBe(false);
  });
});

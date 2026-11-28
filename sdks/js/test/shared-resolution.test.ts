// The language-neutral resolution cases every client SDK must pass
// (spec/client/README.md). JS has no integer read, so integer cases are
// skipped as not applicable.
import { mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { basename, join } from "node:path";
import { fileURLToPath } from "node:url";
import type { JsonValue } from "@openfeature/web-sdk";
import { describe, expect, it } from "vitest";
import type { EvaluateResult } from "../src/internal/options.js";
import { resolveFlag, type FlagType } from "../src/internal/resolve.js";
import { sameJson, storedFrom } from "../src/internal/store.js";

const CLIENT_DIR = fileURLToPath(
  new URL("../../../spec/client", import.meta.url),
);

interface ResolutionCase {
  name: string;
  results: EvaluateResult[] | null;
  source: "network" | "cache";
  flag: string;
  type: FlagType | "integer";
  default: JsonValue;
  expected: {
    value: JsonValue;
    variant: string | null;
    reason: string;
    error_code: string | null;
  };
}

interface CaseFile {
  schema_version: unknown;
  tests: ResolutionCase[];
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

describe("shared resolution cases", () => {
  const file = loadCaseFile(join(CLIENT_DIR, "resolution.json"));

  it("has cases", () => {
    expect(file.tests.length).toBeGreaterThan(0);
  });

  for (const c of file.tests) {
    const type = c.type;
    if (type === "integer") {
      it.skip(`resolution::${c.name} (no integer read)`, () => {});
      continue;
    }
    it(`resolution::${c.name}`, () => {
      const stored =
        c.results === null
          ? undefined
          : storedFrom(c.results, {
              etag: null,
              source: c.source,
              contextHash: "",
              targetingKey: null,
            });
      const got = resolveFlag(stored, c.flag, type, c.default);
      expect(
        sameJson(got.value, c.expected.value),
        `value ${JSON.stringify(got.value)} != ${JSON.stringify(c.expected.value)}`,
      ).toBe(true);
      expect({
        variant: got.variant ?? null,
        reason: got.reason,
        error_code: got.errorCode ?? null,
      }).toEqual({
        variant: c.expected.variant,
        reason: c.expected.reason,
        error_code: c.expected.error_code,
      });
    });
  }
});

describe("shared resolution runner", () => {
  it("refuses a case file with schema_version 2", () => {
    const path = join(mkdtempSync(join(tmpdir(), "megooflags-")), "case.json");
    writeFileSync(
      path,
      '{"schema_version": 2, "kind": "resolution", "tests": []}',
    );
    expect(() => loadCaseFile(path)).toThrow("unsupported schema_version 2");
  });
});

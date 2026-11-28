import { describe, expect, it } from "vitest";
import { parseRuleset } from "../../src/internal/engine/parse.js";
import { type Snapshot, Store } from "../../src/internal/delivery/store.js";

const ruleset = (project: string) =>
  parseRuleset({
    schema_version: 1,
    project,
    environment: "e",
    flags: {},
    segments: {},
  });

describe("Store", () => {
  it("load is undefined before the first swap", () => {
    expect(new Store().load()).toBeUndefined();
  });

  it("swap installs the snapshot and returns the old one", () => {
    const s = new Store();
    const a: Snapshot = { ruleset: ruleset("a"), etag: '"1"', source: "cache" };
    const b: Snapshot = {
      ruleset: ruleset("b"),
      etag: '"2"',
      source: "stream",
    };
    expect(s.swap(a)).toBeUndefined();
    expect(s.load()).toBe(a);
    expect(s.swap(b)).toBe(a);
    expect(s.load()).toBe(b);
  });
});

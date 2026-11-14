// Ruleset builders for the engine tests and the p99 guard.

type Json = Record<string, unknown>;

/**
 * flagJson is a minimal enabled string flag with variations "on" and "off",
 * off_variation "off" and fallthrough "on". fields override or add fields.
 */
export function flagJson(key: string, fields: Json = {}): Json {
  return {
    key,
    type: "string",
    enabled: true,
    version: 1,
    variations: [
      { key: "on", value: "on" },
      { key: "off", value: "off" },
    ],
    off_variation: "off",
    prerequisites: [],
    targets: [],
    rules: [],
    fallthrough: { variation: "on" },
    ...fields,
  };
}

/** ruleFlag has one rule of clauses serving "on", and fallthrough "off". */
export function ruleFlag(key: string, ...clauses: Json[]): Json {
  return flagJson(key, {
    rules: [{ clauses, serve: { variation: "on" } }],
    fallthrough: { variation: "off" },
  });
}

/** cl is a clause. */
export function cl(
  attribute: string,
  operator: string,
  negate: boolean,
  ...values: unknown[]
): Json {
  return { attribute, operator, negate, values };
}

/** rulesetJson wraps flags (keyed by their key) and segments in a ruleset. */
export function rulesetJson(flags: Json[], segments: Json = {}): Json {
  return {
    schema_version: 1,
    project: "p",
    environment: "e",
    flags: Object.fromEntries(flags.map((f) => [f["key"] as string, f])),
    segments,
  };
}

const BENCH_SEGMENTS = 50;

function benchRule(variation: string, ...clauses: Json[]): Json {
  return { description: "", clauses, serve: { variation } };
}

/** flagKeys is flag-0 to flag-<n-1>. */
export function flagKeys(n: number): string[] {
  return Array.from({ length: n }, (_, i) => `flag-${i}`);
}

/**
 * genRuleset is a deterministic ruleset of n flags and 50 segments. Even
 * flags are booleans with three rules (in, matches, semver_gt), a 100-key
 * target list and a three-way fallthrough rollout; odd flags are strings
 * with an in_segment rule and a before rule. Every 10th flag has a
 * prerequisite on the flag before it.
 */
export function genRuleset(n: number): Json {
  const plans = Array.from({ length: 20 }, (_, i) => `plan-${i}`);
  const flags: Json[] = flagKeys(n).map((key, i) => {
    let f: Json;
    if (i % 2 === 0) {
      const targets = Array.from(
        { length: 100 },
        (_, k) => `user-${(i + k * 37) % 1000}`,
      );
      f = flagJson(key, {
        type: "boolean",
        variations: [
          { key: "on", value: true },
          { key: "off", value: false },
          { key: "alt", value: false },
        ],
        targets: [{ variation: "alt", values: targets }],
        rules: [
          benchRule("on", cl("plan", "in", false, ...plans)),
          benchRule("off", cl("email", "matches", false, "^user-[0-9]+$")),
          benchRule("alt", cl("app_version", "semver_gt", false, "1.5.0")),
        ],
        fallthrough: {
          rollout: {
            bucket_by: "key",
            weights: [
              { variation: "on", weight: 33334 },
              { variation: "off", weight: 33333 },
              { variation: "alt", weight: 33333 },
            ],
          },
        },
      });
    } else {
      f = flagJson(key, {
        variations: [
          { key: "on", value: "on" },
          { key: "off", value: "off" },
          { key: "alt", value: "alt" },
        ],
        rules: [
          benchRule(
            "alt",
            cl("key", "in_segment", false, `segment-${i % BENCH_SEGMENTS}`),
          ),
          benchRule(
            "off",
            cl("signed_up", "before", false, "2025-01-01T00:00:00Z"),
          ),
        ],
      });
    }
    if (i > 0 && i % 10 === 0) {
      f["prerequisites"] = [{ flag: `flag-${i - 1}`, variation: "on" }];
    }
    return f;
  });
  const segments: Json = {};
  for (let s = 0; s < BENCH_SEGMENTS; s++) {
    segments[`segment-${s}`] = {
      included: Array.from({ length: 1000 }, (_, k) => `user-${s * 1000 + k}`),
      excluded: [],
      rules: [{ clauses: [cl("org", "in", false, `org-${s}`)] }],
    };
  }
  return {
    ...rulesetJson(flags, segments),
    project: "bench",
    environment: "bench",
  };
}

/** benchContexts are 100 contexts that hit every rule shape of genRuleset. */
export function benchContexts(): Json[] {
  return Array.from({ length: 100 }, (_, j) => ({
    key: `user-${j * 503}`,
    plan: `plan-${j % 40}`,
    email: j % 3 === 0 ? `user-${j}` : `user${j}@example.com`,
    app_version: `1.${j % 10}.0`,
    org: `org-${j % 100}`,
  }));
}

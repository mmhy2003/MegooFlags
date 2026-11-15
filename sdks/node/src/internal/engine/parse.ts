// The ruleset loader: validates a ruleset as in evaluation.md §11 and
// prepares its clause values. Unknown fields are ignored. Prerequisite flags
// and segment keys are not checked: a missing flag fails the prerequisite
// and a missing segment never matches.
import { BUCKETS, type Weight } from "./bucketing.js";
import { parseRfc3339Millis } from "./date.js";
import type {
  Clause,
  Flag,
  FlagType,
  Operator,
  Prepared,
  Prerequisite,
  Rollout,
  Rule,
  Ruleset,
  Scalar,
  Segment,
  Serve,
  Target,
  Variation,
} from "./model.js";
import {
  compilePortable,
  isCompileError,
  type PortableRegex,
} from "./regex.js";
import { parseSemver, type Semver } from "./semver.js";
import { isScalar } from "./value.js";

/** The only ruleset schema_version this engine accepts. */
const SCHEMA_VERSION = 1;

const FLAG_TYPES: ReadonlySet<string> = new Set<FlagType>([
  "boolean",
  "string",
  "number",
  "json",
]);

const OPERATORS: ReadonlySet<string> = new Set<Operator>([
  "in",
  "not_in",
  "contains",
  "starts_with",
  "ends_with",
  "matches",
  "lt",
  "lte",
  "gt",
  "gte",
  "semver_eq",
  "semver_lt",
  "semver_gt",
  "before",
  "after",
  "in_segment",
]);

/** ParseError is an invalid ruleset, with the JSON path of the first problem. */
export class ParseError extends Error {
  override readonly name = "ParseError";

  constructor(
    readonly path: string,
    readonly reason: string,
  ) {
    super(`${path}: ${reason}`);
  }
}

type Obj = Record<string, unknown>;

/**
 * parseRuleset validates a ruleset given as JSON text or as an already
 * decoded tree. It throws a ParseError naming the JSON path of the first
 * problem. Regexes that do not compile are left out and reported in
 * warnings.
 */
export function parseRuleset(textOrTree: unknown): Ruleset {
  let tree = textOrTree;
  if (typeof textOrTree === "string") {
    try {
      tree = JSON.parse(textOrTree);
    } catch (err) {
      throw new ParseError(
        "ruleset",
        `invalid JSON: ${(err as Error).message}`,
      );
    }
  }
  return new Parser().ruleset(tree);
}

class Parser {
  private readonly warnings: string[] = [];

  ruleset(data: unknown): Ruleset {
    const root = asObject(data, "ruleset");
    const version = get(root, "schema_version", "");
    if (typeof version !== "number" || version !== SCHEMA_VERSION) {
      fail("schema_version", `must be ${SCHEMA_VERSION}`);
    }
    const project = asString(get(root, "project", ""), "project");
    const environment = asString(get(root, "environment", ""), "environment");
    const flags = asObject(get(root, "flags", ""), "flags");
    const segments = asObject(get(root, "segments", ""), "segments");
    const flagMap = new Map<string, Flag>();
    for (const key of Object.keys(flags).sort()) {
      flagMap.set(key, this.flag(key, flags[key], `flags.${key}`));
    }
    const segmentMap = new Map<string, Segment>();
    for (const key of Object.keys(segments).sort()) {
      segmentMap.set(key, this.segment(segments[key], `segments.${key}`));
    }
    return {
      project,
      environment,
      flags: flagMap,
      segments: segmentMap,
      warnings: this.warnings,
    };
  }

  private flag(key: string, value: unknown, path: string): Flag {
    const obj = asObject(value, path);
    if (get(obj, "key", path) !== key)
      fail(`${path}.key`, `must equal '${key}'`);
    const type = asString(get(obj, "type", path), `${path}.type`);
    if (!FLAG_TYPES.has(type)) {
      fail(`${path}.type`, `must be one of ${[...FLAG_TYPES].join(", ")}`);
    }
    const variations = parseVariations(
      get(obj, "variations", path),
      `${path}.variations`,
    );
    const off = get(obj, "off_variation", path);
    const offVariation =
      off === null ? null : known(off, variations, `${path}.off_variation`);
    const prerequisites = asList(
      get(obj, "prerequisites", path),
      `${path}.prerequisites`,
    );
    const targets = asList(get(obj, "targets", path), `${path}.targets`);
    const rules = asList(get(obj, "rules", path), `${path}.rules`);
    const enabled = asBool(get(obj, "enabled", path), `${path}.enabled`);
    const version = get(obj, "version", path);
    if (!isInt64(version)) {
      fail(`${path}.version`, "must be an integer within the int64 range");
    }
    return {
      key,
      type: type as FlagType,
      enabled,
      version,
      variations,
      offVariation,
      prerequisites: prerequisites.map((p, i): Prerequisite => {
        const itemPath = `${path}.prerequisites[${i}]`;
        const o = asObject(p, itemPath);
        return {
          flag: asString(get(o, "flag", itemPath), `${itemPath}.flag`),
          variation: asString(
            get(o, "variation", itemPath),
            `${itemPath}.variation`,
          ),
        };
      }),
      targets: targets.map((t, i): Target => {
        const itemPath = `${path}.targets[${i}]`;
        const o = asObject(t, itemPath);
        return {
          variation: known(
            get(o, "variation", itemPath),
            variations,
            `${itemPath}.variation`,
          ),
          values: stringSet(get(o, "values", itemPath), `${itemPath}.values`),
        };
      }),
      rules: rules.map((r, i) =>
        this.rule(r, variations, `${path}.rules[${i}]`),
      ),
      fallthrough: parseServe(
        get(obj, "fallthrough", path),
        variations,
        `${path}.fallthrough`,
      ),
    };
  }

  private rule(
    value: unknown,
    variations: ReadonlyMap<string, Variation>,
    path: string,
  ): Rule {
    const obj = asObject(value, path);
    const description = Object.hasOwn(obj, "description")
      ? asString(obj["description"], `${path}.description`)
      : "";
    return {
      description,
      clauses: this.clauses(
        get(obj, "clauses", path),
        `${path}.clauses`,
        false,
      ),
      serve: parseServe(get(obj, "serve", path), variations, `${path}.serve`),
    };
  }

  private clauses(value: unknown, path: string, inSegment: boolean): Clause[] {
    return asList(value, path, true).map((item, i) =>
      this.clause(item, `${path}[${i}]`, inSegment),
    );
  }

  private clause(value: unknown, path: string, inSegment: boolean): Clause {
    const obj = asObject(value, path);
    const attribute = asString(
      get(obj, "attribute", path),
      `${path}.attribute`,
    );
    const operator = asString(get(obj, "operator", path), `${path}.operator`);
    if (!OPERATORS.has(operator)) {
      fail(`${path}.operator`, `unknown operator ${JSON.stringify(operator)}`);
    }
    if (inSegment && operator === "in_segment") {
      fail(`${path}.operator`, "in_segment is not allowed in a segment rule");
    }
    const valuesPath = `${path}.values`;
    const values = asList(get(obj, "values", path), valuesPath, true);
    values.forEach((v, i) => {
      if (!isScalar(v)) {
        fail(`${valuesPath}[${i}]`, "must be a string, number or boolean");
      }
    });
    const negate = Object.hasOwn(obj, "negate")
      ? asBool(obj["negate"], `${path}.negate`)
      : false;
    const scalars = values as Scalar[];
    return {
      attribute,
      operator: operator as Operator,
      negate,
      values: scalars,
      prepared: this.prepare(operator as Operator, scalars, valuesPath),
    };
  }

  /** prepare keeps the values the operator can use. */
  private prepare(op: Operator, values: Scalar[], path: string): Prepared {
    switch (op) {
      case "in":
      case "not_in":
        return { kind: "equal", values };
      case "contains":
      case "starts_with":
      case "ends_with":
        return { kind: "strings", values: strings(values) };
      case "matches": {
        const regexes: PortableRegex[] = [];
        values.forEach((v, i) => {
          if (typeof v !== "string") return;
          const re = compilePortable(v);
          if (isCompileError(re)) {
            this.warnings.push(
              `${path}[${i}]: regex does not compile: ${re.reason}`,
            );
          } else {
            regexes.push(re);
          }
        });
        return { kind: "regex", values: regexes };
      }
      case "lt":
      case "lte":
      case "gt":
      case "gte":
        return {
          kind: "numbers",
          values: values.filter((v): v is number => typeof v === "number"),
        };
      case "semver_eq":
      case "semver_lt":
      case "semver_gt":
        return {
          kind: "semver",
          values: strings(values)
            .map(parseSemver)
            .filter((v): v is Semver => v !== undefined),
        };
      case "before":
      case "after":
        // Only a string is a date here; a number value never matches.
        return {
          kind: "millis",
          values: strings(values)
            .map(parseRfc3339Millis)
            .filter((v): v is number => v !== undefined),
        };
      case "in_segment":
        return { kind: "segments", values: strings(values) };
    }
  }

  private segment(value: unknown, path: string): Segment {
    const obj = asObject(value, path);
    const rulesPath = `${path}.rules`;
    const rules = asList(get(obj, "rules", path), rulesPath);
    return {
      included: stringSet(get(obj, "included", path), `${path}.included`),
      excluded: stringSet(get(obj, "excluded", path), `${path}.excluded`),
      rules: rules.map((r, i) => {
        const rulePath = `${rulesPath}[${i}]`;
        const rule = asObject(r, rulePath);
        return this.clauses(
          get(rule, "clauses", rulePath),
          `${rulePath}.clauses`,
          true,
        );
      }),
    };
  }
}

function parseVariations(value: unknown, path: string): Map<string, Variation> {
  const variations = new Map<string, Variation>();
  asList(value, path, true).forEach((item, i) => {
    const itemPath = `${path}[${i}]`;
    const obj = asObject(item, itemPath);
    const key = asString(get(obj, "key", itemPath), `${itemPath}.key`);
    if (variations.has(key)) {
      fail(`${itemPath}.key`, `duplicate variation '${key}'`);
    }
    variations.set(key, { key, value: get(obj, "value", itemPath) });
  });
  return variations;
}

function parseServe(
  value: unknown,
  variations: ReadonlyMap<string, Variation>,
  path: string,
): Serve {
  const obj = asObject(value, path);
  const hasVariation = Object.hasOwn(obj, "variation");
  if (hasVariation === Object.hasOwn(obj, "rollout")) {
    fail(path, "must have exactly one of variation or rollout");
  }
  if (hasVariation) {
    return {
      variation: known(obj["variation"], variations, `${path}.variation`),
      rollout: null,
    };
  }
  return {
    variation: null,
    rollout: parseRollout(obj["rollout"], variations, `${path}.rollout`),
  };
}

function parseRollout(
  value: unknown,
  variations: ReadonlyMap<string, Variation>,
  path: string,
): Rollout {
  const obj = asObject(value, path);
  const bucketBy = asString(get(obj, "bucket_by", path), `${path}.bucket_by`);
  const weightsPath = `${path}.weights`;
  let sum = 0;
  const weights = asList(get(obj, "weights", path), weightsPath, true).map(
    (item, i): Weight => {
      const itemPath = `${weightsPath}[${i}]`;
      const w = asObject(item, itemPath);
      const variation = known(
        get(w, "variation", itemPath),
        variations,
        `${itemPath}.variation`,
      );
      const weight = get(w, "weight", itemPath);
      if (
        !Number.isInteger(weight) ||
        (weight as number) < 0 ||
        (weight as number) > BUCKETS
      ) {
        fail(
          `${itemPath}.weight`,
          `must be an integer between 0 and ${BUCKETS}`,
        );
      }
      sum += weight as number;
      return { variation, weight: weight as number };
    },
  );
  if (sum !== BUCKETS) fail(weightsPath, `must add up to ${BUCKETS}`);
  return { bucketBy, weights };
}

function fail(path: string, reason: string): never {
  throw new ParseError(path, reason);
}

/** get returns the own property obj[name], failing with "<path>.<name>: is required". */
function get(obj: Obj, name: string, path: string): unknown {
  if (!Object.hasOwn(obj, name)) {
    fail(path === "" ? name : `${path}.${name}`, "is required");
  }
  return obj[name];
}

/** asObject accepts a non-null, non-array object. */
function asObject(v: unknown, path: string): Obj {
  if (typeof v !== "object" || v === null || Array.isArray(v)) {
    fail(path, "must be an object");
  }
  return v as Obj;
}

function asList(v: unknown, path: string, nonEmpty = false): unknown[] {
  if (!Array.isArray(v)) fail(path, "must be an array");
  if (nonEmpty && v.length === 0) fail(path, "must not be empty");
  return v;
}

function asString(v: unknown, path: string): string {
  if (typeof v !== "string") fail(path, "must be a string");
  return v;
}

function asBool(v: unknown, path: string): boolean {
  if (typeof v !== "boolean") fail(path, "must be a boolean");
  return v;
}

/** isInt64 reports whether v is an integral number within the int64 range. */
function isInt64(v: unknown): v is number {
  return (
    Number.isInteger(v) &&
    (v as number) >= -(2 ** 63) &&
    (v as number) < 2 ** 63
  );
}

function stringSet(v: unknown, path: string): Set<string> {
  return new Set(
    asList(v, path).map((item, i) => asString(item, `${path}[${i}]`)),
  );
}

function strings(values: readonly Scalar[]): string[] {
  return values.filter((v): v is string => typeof v === "string");
}

function known(
  v: unknown,
  variations: ReadonlyMap<string, Variation>,
  path: string,
): string {
  const key = asString(v, path);
  if (!variations.has(key)) fail(path, `unknown variation '${key}'`);
  return key;
}

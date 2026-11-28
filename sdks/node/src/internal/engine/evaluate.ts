// Flag evaluation (evaluation.md §3 to §10). evaluate is pure: it reads the
// ruleset and the context and changes neither.
import { bucket, bucketText, pickVariation } from "./bucketing.js";
import { toMillis } from "./date.js";
import type { Clause, Flag, Ruleset, Segment, Serve } from "./model.js";
import { compareSemver, parseSemver } from "./semver.js";
import { isScalar, jsonEqual } from "./value.js";

/** The deepest prerequisite chain evaluate follows; deeper, or a cycle, is an error. */
export const MAX_PREREQUISITE_DEPTH = 32;

export type Reason =
  | "STATIC"
  | "DEFAULT"
  | "TARGETING_MATCH"
  | "SPLIT"
  | "DISABLED"
  | "PREREQUISITE_FAILED"
  | "ERROR";

export type ErrorCode =
  | "FLAG_NOT_FOUND"
  | "TYPE_MISMATCH"
  | "PARSE_ERROR"
  | "INVALID_CONTEXT"
  | "GENERAL";

export type ExpectedType = "boolean" | "string" | "number" | "json";

/**
 * Result is the resolution of one flag. variant is null when the default
 * was returned: on an error, or when the variation to serve is a null
 * off_variation.
 */
export interface Result {
  value: unknown;
  variant: string | null;
  reason: Reason;
  errorCode: ErrorCode | null;
  errorMessage: string;
  /** The matching rule. */
  ruleIndex: number | null;
  /** The failed prerequisite flag. */
  prerequisite: string | null;
}

export interface Metadata {
  rule_index?: number;
  prerequisite?: string;
}

/** metadata is {rule_index} and/or {prerequisite} when set, {} otherwise. */
export function metadata(r: Result): Metadata {
  const m: Metadata = {};
  if (r.ruleIndex !== null) m.rule_index = r.ruleIndex;
  if (r.prerequisite !== null) m.prerequisite = r.prerequisite;
  return m;
}

type Context = Record<string, unknown>;

/** Thrown inside evaluate when the prerequisite chain is too deep. */
const TOO_DEEP = Symbol("too deep");

/**
 * evaluate resolves flagKey for context, which must be a JSON object.
 * Errors return defaultValue with reason ERROR. expectedType, when given, is
 * checked only against a served variation.
 */
export function evaluate(
  ruleset: Ruleset,
  flagKey: string,
  context: unknown,
  defaultValue: unknown,
  expectedType?: ExpectedType,
): Result {
  if (
    typeof context !== "object" ||
    context === null ||
    Array.isArray(context)
  ) {
    return errorResult(
      defaultValue,
      "INVALID_CONTEXT",
      "context must be a JSON object",
    );
  }
  const flag = ruleset.flags.get(flagKey);
  if (flag === undefined) {
    return errorResult(
      defaultValue,
      "FLAG_NOT_FOUND",
      `flag '${flagKey}' not found`,
    );
  }
  let res: Result;
  try {
    res = evalFlag(ruleset, flag, context as Context, defaultValue, 0);
  } catch (err) {
    if (err !== TOO_DEEP) throw err;
    return errorResult(
      defaultValue,
      "GENERAL",
      "prerequisite chain is too deep or has a cycle",
    );
  }
  if (
    expectedType !== undefined &&
    res.variant !== null &&
    !hasType(res.value, expectedType)
  ) {
    return errorResult(
      defaultValue,
      "TYPE_MISMATCH",
      `flag '${flagKey}' is not a ${expectedType}`,
    );
  }
  return res;
}

function evalFlag(
  rs: Ruleset,
  flag: Flag,
  ctx: Context,
  def: unknown,
  depth: number,
): Result {
  if (!flag.enabled) return serveOff(flag, "DISABLED", def);
  for (const p of flag.prerequisites) {
    if (!prerequisiteMet(rs, p.flag, p.variation, ctx, depth + 1)) {
      const res = serveOff(flag, "PREREQUISITE_FAILED", def);
      res.prerequisite = p.flag;
      return res;
    }
  }
  const key = attr(ctx, "key");
  if (typeof key === "string") {
    for (const t of flag.targets) {
      if (t.values.has(key))
        return serve(flag, t.variation, "TARGETING_MATCH", def);
    }
  }
  for (let i = 0; i < flag.rules.length; i++) {
    const rule = flag.rules[i]!;
    if (allClausesMatch(rs, rule.clauses, ctx)) {
      const res = serveRule(flag, rule.serve, ctx, "TARGETING_MATCH", def);
      res.ruleIndex = i;
      return res;
    }
  }
  let reason: Reason = "DEFAULT";
  if (flag.fallthrough.rollout !== null) reason = "SPLIT";
  else if (
    flag.targets.length === 0 &&
    flag.rules.length === 0 &&
    flag.prerequisites.length === 0
  ) {
    reason = "STATIC";
  }
  return serveRule(flag, flag.fallthrough, ctx, reason, def);
}

/** prerequisiteMet throws TOO_DEEP when depth is over MAX_PREREQUISITE_DEPTH. */
function prerequisiteMet(
  rs: Ruleset,
  flagKey: string,
  variation: string,
  ctx: Context,
  depth: number,
): boolean {
  if (depth > MAX_PREREQUISITE_DEPTH) throw TOO_DEEP;
  const pf = rs.flags.get(flagKey);
  if (pf === undefined || !pf.enabled) return false;
  const res = evalFlag(rs, pf, ctx, null, depth);
  return res.reason !== "ERROR" && res.variant === variation;
}

function allClausesMatch(
  rs: Ruleset,
  clauses: readonly Clause[],
  ctx: Context,
): boolean {
  for (const c of clauses) if (!clauseMatches(rs, c, ctx)) return false;
  return true;
}

function clauseMatches(rs: Ruleset, c: Clause, ctx: Context): boolean {
  if (c.prepared.kind === "segments") {
    let hit = false;
    for (const k of c.prepared.values) {
      if (inSegment(rs, rs.segments.get(k), ctx)) {
        hit = true;
        break;
      }
    }
    return hit !== c.negate;
  }
  const v = attr(ctx, c.attribute);
  if (v === undefined || v === null) return false; // negate not applied
  const hit = Array.isArray(v)
    ? v.some((e) => matchValue(c, e))
    : matchValue(c, v);
  return hit !== c.negate;
}

function inSegment(rs: Ruleset, s: Segment | undefined, ctx: Context): boolean {
  if (s === undefined) return false;
  const key = attr(ctx, "key");
  if (typeof key === "string") {
    if (s.excluded.has(key)) return false;
    if (s.included.has(key)) return true;
  }
  return s.rules.some((clauses) => allClausesMatch(rs, clauses, ctx));
}

/** matchValue reports whether one element of the attribute matches the clause. */
function matchValue(c: Clause, e: unknown): boolean {
  const p = c.prepared;
  switch (p.kind) {
    case "equal":
      if (c.operator === "in") return p.values.some((x) => jsonEqual(e, x));
      return isScalar(e) && !p.values.some((x) => jsonEqual(e, x));
    case "strings": {
      if (typeof e !== "string") return false;
      switch (c.operator) {
        case "contains":
          return p.values.some((x) => e.includes(x));
        case "starts_with":
          return p.values.some((x) => e.startsWith(x));
        default:
          return p.values.some((x) => e.endsWith(x));
      }
    }
    case "regex":
      return typeof e === "string" && p.values.some((re) => re.test(e));
    case "numbers": {
      if (typeof e !== "number") return false;
      switch (c.operator) {
        case "lt":
          return p.values.some((x) => e < x);
        case "lte":
          return p.values.some((x) => e <= x);
        case "gt":
          return p.values.some((x) => e > x);
        default:
          return p.values.some((x) => e >= x);
      }
    }
    case "semver": {
      if (typeof e !== "string") return false;
      const sv = parseSemver(e);
      if (sv === undefined) return false;
      const want =
        c.operator === "semver_lt" ? -1 : c.operator === "semver_gt" ? 1 : 0;
      return p.values.some((x) => compareSemver(sv, x) === want);
    }
    case "millis": {
      const t = toMillis(e);
      if (t === undefined) return false;
      return c.operator === "before"
        ? p.values.some((x) => t < x)
        : p.values.some((x) => t > x);
    }
    case "segments":
      return false; // handled by clauseMatches
  }
}

/** attr is the own property name of the context, so inherited names are missing. */
function attr(ctx: Context, name: string): unknown {
  return Object.hasOwn(ctx, name) ? ctx[name] : undefined;
}

/** hasType reports whether a served value has the type asked for. */
function hasType(v: unknown, expectedType: ExpectedType): boolean {
  switch (expectedType) {
    case "boolean":
      return typeof v === "boolean";
    case "string":
      return typeof v === "string";
    case "number":
      return typeof v === "number";
    case "json":
      return true;
  }
}

function serveOff(flag: Flag, reason: Reason, def: unknown): Result {
  if (flag.offVariation === null) return result(def, null, reason);
  return serve(flag, flag.offVariation, reason, def);
}

function serve(
  flag: Flag,
  variation: string,
  reason: Reason,
  def: unknown,
): Result {
  const v = flag.variations.get(variation);
  // parseRuleset guarantees known variations; this keeps a bad Ruleset from throwing.
  if (v === undefined) return result(def, null, reason);
  return result(v.value, variation, reason);
}

function serveRule(
  flag: Flag,
  s: Serve,
  ctx: Context,
  reason: Reason,
  def: unknown,
): Result {
  if (s.rollout === null)
    return serve(flag, s.variation as string, reason, def);
  const text = bucketText(attr(ctx, s.rollout.bucketBy));
  const b = text === undefined ? 0 : bucket(flag.key, text);
  const variation = pickVariation(s.rollout.weights, b);
  if (variation === undefined) return result(def, null, reason);
  return serve(flag, variation, reason, def);
}

function result(
  value: unknown,
  variant: string | null,
  reason: Reason,
): Result {
  return {
    value,
    variant,
    reason,
    errorCode: null,
    errorMessage: "",
    ruleIndex: null,
    prerequisite: null,
  };
}

function errorResult(def: unknown, code: ErrorCode, message: string): Result {
  return {
    ...result(def, null, "ERROR"),
    errorCode: code,
    errorMessage: message,
  };
}

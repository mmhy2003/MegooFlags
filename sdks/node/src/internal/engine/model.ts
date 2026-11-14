// A parsed, validated environment ruleset (spec/ruleset.schema.json). It is
// read-only once parseRuleset returns it. Flags, segments and variations are
// Maps and key lists are Sets, so a key named __proto__ or constructor is
// an ordinary key.
import type { Weight } from "./bucketing.js";
import type { PortableRegex } from "./regex.js";
import type { Semver } from "./semver.js";

export type FlagType = "boolean" | "string" | "number" | "json";

export type Operator =
  | "in"
  | "not_in"
  | "contains"
  | "starts_with"
  | "ends_with"
  | "matches"
  | "lt"
  | "lte"
  | "gt"
  | "gte"
  | "semver_eq"
  | "semver_lt"
  | "semver_gt"
  | "before"
  | "after"
  | "in_segment";

export type Scalar = string | number | boolean;

export interface Ruleset {
  readonly project: string;
  readonly environment: string;
  readonly flags: ReadonlyMap<string, Flag>;
  readonly segments: ReadonlyMap<string, Segment>;
  /** Problems that do not refuse the ruleset, such as a regex that does not compile. */
  readonly warnings: readonly string[];
}

export interface Flag {
  readonly key: string;
  readonly type: FlagType;
  readonly enabled: boolean;
  readonly version: number;
  /** In ruleset order. */
  readonly variations: ReadonlyMap<string, Variation>;
  /** null when off_variation is null. */
  readonly offVariation: string | null;
  readonly prerequisites: readonly Prerequisite[];
  readonly targets: readonly Target[];
  readonly rules: readonly Rule[];
  readonly fallthrough: Serve;
}

/** Variation is one possible value of a flag; value is any JSON, null included. */
export interface Variation {
  readonly key: string;
  readonly value: unknown;
}

/** Prerequisite requires flag to serve variation. */
export interface Prerequisite {
  readonly flag: string;
  readonly variation: string;
}

/** Target serves variation to the context keys in values. */
export interface Target {
  readonly variation: string;
  readonly values: ReadonlySet<string>;
}

/** Rule serves serve when all of its clauses match. */
export interface Rule {
  readonly description: string;
  readonly clauses: readonly Clause[];
  readonly serve: Serve;
}

/** Serve is either a fixed variation or a rollout: exactly one is not null. */
export interface Serve {
  readonly variation: string | null;
  readonly rollout: Rollout | null;
}

/** Rollout splits contexts by the bucket of attribute bucketBy. */
export interface Rollout {
  readonly bucketBy: string;
  readonly weights: readonly Weight[];
}

/**
 * Segment matches a context that is not excluded and is included or
 * matches any rule (each rule is a list of clauses that must all match).
 */
export interface Segment {
  readonly included: ReadonlySet<string>;
  readonly excluded: ReadonlySet<string>;
  readonly rules: readonly (readonly Clause[])[];
}

/** Clause tests one context attribute with an operator. */
export interface Clause {
  readonly attribute: string;
  readonly operator: Operator;
  readonly negate: boolean;
  readonly values: readonly Scalar[];
  /**
   * The values the operator can use. The others are left out, which is the
   * same as "never matches" (evaluation.md §11).
   */
  readonly prepared: Prepared;
}

export type Prepared =
  | { readonly kind: "equal"; readonly values: readonly Scalar[] } // in, not_in
  | { readonly kind: "strings"; readonly values: readonly string[] } // contains, starts_with, ends_with
  | { readonly kind: "regex"; readonly values: readonly PortableRegex[] } // matches
  | { readonly kind: "numbers"; readonly values: readonly number[] } // lt, lte, gt, gte
  | { readonly kind: "semver"; readonly values: readonly Semver[] } // semver_eq, semver_lt, semver_gt
  | { readonly kind: "millis"; readonly values: readonly number[] } // before, after
  | { readonly kind: "segments"; readonly values: readonly string[] }; // in_segment

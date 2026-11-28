# MegooFlags evaluation spec

Version 1 (ruleset `schema_version: 1`).

This document defines how a flag is evaluated for a context. The server, through the reference engine in
`api/src/megooflags_api/evaluation/`, and every SDK must give the same result for the same ruleset and
context. The [conformance suite](conformance/) checks this, and every SDK must pass it in CI.

The key words MUST, MUST NOT and SHOULD are used as in RFC 2119.

## 1. Context (T-1)

The evaluation context is a JSON object of arbitrary attributes.

- The OpenFeature `targetingKey` maps to the attribute `key`. A provider MUST copy `targetingKey` into
  `key` before it evaluates.
- An attribute name refers to a top-level key of the context only. There are no nested or dotted paths:
  the attribute `a.b` is the top-level key `"a.b"`.
- An attribute value is a scalar (string, number or boolean) or an array of scalars.
- **Null is missing.** An attribute whose value is JSON `null` counts as missing.
- **Invalid context.** If the context is not a JSON object, the caller's default is returned with reason
  `ERROR` and error code `INVALID_CONTEXT`.

## 2. Result

An evaluation returns OpenFeature resolution details:

| Field | Meaning |
|---|---|
| `value` | The served variation value, or the caller's default |
| `variant` | The served variation key, or `null` when no variation was served |
| `reason` | One of the reasons below |
| `error_code` | `null`, or one of the error codes in section 5 |
| `error_message` | A human-readable message when `error_code` is set. Its text is not normative |
| `metadata` | An object. `rule_index` and `prerequisite` are defined below |

Reasons: `STATIC`, `DEFAULT`, `TARGETING_MATCH`, `SPLIT`, `DISABLED`, `PREREQUISITE_FAILED` (a custom
reason) and `ERROR`.

## 3. Order of evaluation

The first step that applies decides the result.

| Step | What happens | Reason |
|---|---|---|
| 0 | The context is not a JSON object: the caller's default is returned, error `INVALID_CONTEXT` | `ERROR` |
| 1 | Flag missing or archived (archived flags are not in the ruleset): the caller's default is returned, error `FLAG_NOT_FOUND` | `ERROR` |
| 2 | Flag disabled: the off variation is served | `DISABLED` |
| 3 | Prerequisites, in order: each one is evaluated recursively with the same context (section 9). The first one that fails serves the off variation, and `metadata.prerequisite` is its flag key | `PREREQUISITE_FAILED` |
| 4 | Individual targets (T-2), in order: the context `key` is in a target's `values` | `TARGETING_MATCH` |
| 5 | Rules, in order: the first rule whose clauses all match (AND) is served, whether it serves a fixed variation or a rollout. `metadata.rule_index` is the rule's 0-based position | `TARGETING_MATCH` |
| 6 | Fallthrough with a rollout | `SPLIT` |
| 6 | Fixed fallthrough on a flag with no targets, no rules and no prerequisites | `STATIC` |
| 6 | Any other fixed fallthrough | `DEFAULT` |

**Individual targets.** Targets match only when the context `key` is a string, compared exactly
(case-sensitive). A number `42` does not match the target value `"42"`. Targets are checked in their
listed order, and the first target whose `values` contain the key wins.

## 4. Off variation and type checks

- **Missing off variation.** If the off variation must be served (steps 2 and 3) and the flag's
  `off_variation` is `null`, the caller's default is returned with the same reason (`DISABLED` or
  `PREREQUISITE_FAILED`), `variant: null` and no error code.
- **Null values.** A JSON `null` variation value is a valid value. Serving it sets `variant`, and it is
  distinct from a missing off variation.
- **Expected type.** The caller MAY pass an expected type: `boolean`, `string`, `number` or `json`.
  - `boolean` accepts a JSON boolean, `string` a JSON string, and `number` any JSON number (integer or
    fraction) but not a boolean. `json` accepts any value, `null` included.
  - The check runs only when a variation was served (`variant` is not `null`). An error result, or a
    missing off variation, returns the default unchecked.
  - If the served value does not have the expected type, the default is returned with reason `ERROR` and
    error code `TYPE_MISMATCH`.
  - Converting a number to an integer or a float is left to each SDK.

## 5. Error codes

Error codes are OpenFeature's. Every error returns the caller's default, `variant: null`, reason `ERROR`
and empty metadata.

| Code | When |
|---|---|
| `FLAG_NOT_FOUND` | The flag key is not in the ruleset (it does not exist or is archived) |
| `TYPE_MISMATCH` | The served value does not have the expected type (section 4) |
| `PARSE_ERROR` | The ruleset is invalid (section 11). This is raised when the ruleset is loaded, before any flag is evaluated |
| `INVALID_CONTEXT` | The context is not a JSON object |
| `GENERAL` | The prerequisite depth guard tripped (section 9) |

## 6. Clauses

A rule holds one or more clauses, and every clause must match (AND). A clause has an `attribute`, an
`operator`, a non-empty list of scalar `values` and a `negate` flag.

1. **Any value.** A clause matches if **any** of its values matches (OR).
2. **Missing attribute.** If the attribute is missing or `null`, the clause is false and `negate` is
   **not** applied.
3. **Negate.** Otherwise `negate` inverts the result.
4. **Wrong type.** An attribute of the wrong type for the operator makes the match false, and `negate`
   still applies (so a negated clause on a wrong-typed attribute is true).
5. **Arrays.** An array attribute matches if any element matches. An empty array is present but matches
   nothing, so `negate` applies to it. An element that is an object or an array is the wrong type and
   matches nothing.
6. **Unusable values never match.** A clause value that cannot be prepared for its operator never
   matches: a regex that does not compile, an invalid semver, an unparsable date, or a value of the
   wrong type for the operator (for example a number in `contains`). The other values of the clause are
   still tried.

The rest of this section describes when a single attribute element matches a single clause value.

### 6.1 Equality: `in`, `not_in`

Strict JSON equality:

- a boolean equals only a boolean of the same value (`true` does not equal `1`);
- numbers compare numerically (`1` equals `1.0`);
- a string equals only the same string (`"1"` does not equal `1`, case-sensitive).

`in` is true when the element equals any value. **`not_in`** is true when the element is a scalar that
equals **none** of the values. With an array attribute, `not_in` therefore matches if any element is
not listed.

### 6.2 Strings: `contains`, `starts_with`, `ends_with`

Strings only, case-sensitive, compared by code point. A non-string element or value never matches.

### 6.3 Regular expressions: `matches`

An unanchored search (`^` and `$` anchor explicitly) on a string element, with the **portable regex
subset**:

- the syntax common to RE2 and Python `re`;
- ASCII semantics: `\d`, `\w`, `\s` and `\b` and case-insensitive matching cover ASCII only (Python
  `re.ASCII`), so `\d` does not match `"٣"`;
- **not portable**, and refused when a rule is saved: lookahead `(?=` `(?!`, lookbehind `(?<=` `(?<!`,
  backreferences `\1`..`\9` and `(?P=name)`, atomic groups `(?>`, possessive quantifiers `*+` `++` `?+`
  `{n}+`, and conditionals `(?(`.

A stored pattern that does not compile never matches (rule 6).

### 6.4 Numbers: `lt`, `lte`, `gt`, `gte`

Numbers only: an element that is a number (not a boolean) compared with a number value as
`element < value`, `<=`, `>`, `>=`. Strings are never coerced: `"5"` does not match `lt 10`.

### 6.5 Semantic versions: `semver_eq`, `semver_lt`, `semver_gt`

SemVer 2.0.0 precedence (§11). Build metadata is ignored, so `1.0.0+b1` equals `1.0.0+b2`.

- A version is a string that fully matches the SemVer 2.0.0 grammar: no `v` prefix, no leading zeros,
  exactly three core numbers. Matching is ASCII-only and over the whole string (`fullmatch`), so
  `"1.2.3\n"` and Unicode digits are invalid.
- An invalid attribute version is a non-match. An invalid clause value never matches (rule 6).
- `semver_eq` is precedence equality, `semver_lt` is `element < value` and `semver_gt` is
  `element > value`.

### 6.6 Dates: `before`, `after`

Both sides become integer **milliseconds** since the Unix epoch, and they are compared as instants:
`before` means `element < value`, and `after` means `element > value`.

- An attribute is either an RFC 3339 date-time string or a number of Unix epoch milliseconds.
  - A number is floored to an integer (`1500.9` → 1500, `-0.5` → -1). Non-finite numbers are invalid.
  - A string must fully match (ASCII, `fullmatch`)
    `^\d{4}-\d{2}-\d{2}[Tt ]\d{2}:\d{2}:\d{2}(\.\d+)?([Zz]|[+-]\d{2}:\d{2})$` and be a real date and time.
    A fraction is truncated to milliseconds (`.123456789` → 123 ms). A date without a time, such as
    `2026-01-01`, is invalid.
- A clause value MUST be an RFC 3339 string. A number value or an invalid string never matches.
- An invalid attribute is a non-match (so a negated clause is true).

### 6.7 Segments: `in_segment`

See section 7.

## 7. Segments (T-4)

`in_segment` matches if the context is in **any** of the segments whose keys are listed in `values`.

- It ignores `attribute` and the missing-attribute rule. `negate` applies.
- An unknown segment key is never matched.

A context is in a segment when, in this order:

1. its `key` is a string in the segment's `excluded` list: it is **not** in the segment;
2. otherwise, its `key` is a string in the `included` list: it **is** in the segment;
3. otherwise, it is in the segment if any segment rule matches (all of the rule's clauses match).

Steps 1 and 2 only apply when the context `key` is a string; a context without `key` goes straight to
the rules. A segment rule MUST NOT use `in_segment`.

## 8. Rollouts and bucketing (T-5)

A serve is either a fixed `variation` or a `rollout` with `bucket_by` and `weights`. Weights are integers
from 0 to 100000 that add up to 100000 (100000 = 100 %, so 1 = 0.001 %).

**Bucketing value.** The value of the `bucket_by` attribute (`key` by default):

- a string is used as is;
- an integral number (an integer, or a float with an integral value) with |n| ≤ 2^53−1
  (9007199254740991) is written as base-10 text without a fraction or exponent: `42` and `42.0` give
  `"42"`, and `-0.0` gives `"0"`;
- anything else (a boolean, another number, an object, an array, `null` or a missing attribute) counts as
  **missing**.

**Bucket.**

```
bucket = uint64_be(sha256(utf8("<flag_key>.<bucketing value>"))[0:8]) mod 100000
```

That is: hash the UTF-8 bytes of the flag key, a `.`, and the bucketing value text with SHA-256, read the
first 8 bytes of the digest as an unsigned big-endian 64-bit integer, and take it modulo 100000. A
missing bucketing value gives bucket **0**, so the first variation with a non-zero weight is served.

There is no rule-position salt: a context lands in the same bucket in every rule and in the fallthrough
of one flag. Raising a variation's percentage only adds contexts to it; it never moves the ones already
in.

**Variation.** Walk the weights in their listed order, keeping a running total. The first variation
whose running total is **greater than** the bucket is served.

**Worked example** (vector `"f.u"` of `conformance/bucketing.json`):

1. Flag `f`, context `{"key": "u"}`, `bucket_by: "key"`: the hash input is `f.u`.
2. `sha256("f.u")` = `fbf3509bcdd6ea42fcd82573624486b68d14d1956a537a2d287013cbdb2281d6`.
3. The first 8 bytes are `fbf3509bcdd6ea42` = 18154943152964168258.
4. 18154943152964168258 mod 100000 = **68258**.
5. With weights `[{"variation": "a", "weight": 30000}, {"variation": "b", "weight": 40000},
   {"variation": "c", "weight": 30000}]` the running totals are 30000, 70000, 100000. 70000 is the first
   total greater than 68258, so `b` is served.

You can check step 2 with `printf '%s' 'f.u' | sha256sum`.

A rule that serves a rollout gives `TARGETING_MATCH` with `metadata.rule_index`; a fallthrough rollout
gives `SPLIT`.

## 9. Prerequisites (F-8)

A flag's prerequisites are checked in their listed order, after the enabled check and before targets.
Each one names a flag and a required variation key.

- A prerequisite is evaluated recursively with the same context, following this whole spec from step 2.
- It **fails** if its flag is missing from the ruleset, is disabled, evaluates with reason `ERROR`, or
  serves a variant other than the required one.
- The first failing prerequisite makes the flag serve its off variation with reason
  `PREREQUISITE_FAILED` and `metadata.prerequisite` set to the failing flag key.
- Only the variant counts. A prerequisite whose own prerequisite fails, but whose off variation equals
  the required variation, **passes**.
- **Depth guard.** `MAX_PREREQUISITE_DEPTH = 32`. Checking a prerequisite more than 32 levels below the
  evaluated flag makes the **whole** evaluation return the caller's default with reason `ERROR` and error
  code `GENERAL`. Cycles are refused when a prerequisite is saved, so this can only trip on a malformed
  ruleset.

## 10. Ruleset format

Server SDKs download one ruleset per environment. It is a JSON object described by
[`ruleset.schema.json`](ruleset.schema.json) (JSON Schema 2020-12). Names are snake_case.

```json
{
  "schema_version": 1,
  "project": "shop",
  "environment": "production",
  "flags": {
    "new-checkout": {
      "key": "new-checkout",
      "type": "boolean",
      "enabled": true,
      "version": 7,
      "variations": [{"key": "on", "value": true}, {"key": "off", "value": false}],
      "off_variation": "off",
      "prerequisites": [{"flag": "payments-v2", "variation": "on"}],
      "targets": [{"variation": "on", "values": ["qa-1", "qa-2"]}],
      "rules": [
        {
          "description": "Beta testers",
          "clauses": [
            {"attribute": "key", "operator": "in_segment", "values": ["beta"], "negate": false}
          ],
          "serve": {"variation": "on"}
        },
        {
          "description": "Recent app versions",
          "clauses": [
            {"attribute": "app_version", "operator": "semver_gt", "values": ["2.0.0"], "negate": false}
          ],
          "serve": {
            "rollout": {
              "bucket_by": "org",
              "weights": [{"variation": "on", "weight": 25000}, {"variation": "off", "weight": 75000}]
            }
          }
        }
      ],
      "fallthrough": {"variation": "off"}
    }
  },
  "segments": {
    "beta": {
      "included": ["u1"],
      "excluded": ["u2"],
      "rules": [
        {"clauses": [{"attribute": "plan", "operator": "in", "values": ["pro"], "negate": false}]}
      ]
    }
  }
}
```

- `flags` and `segments` are keyed by flag and segment key, and a flag's `key` equals its dictionary key.
- Archived flags are left out.
- References use keys: variations, prerequisites (`flag`, `variation`) and segments. For `in_segment`,
  `values` holds the segment keys.
- `type` is `boolean`, `string`, `number` or `json`.
- `off_variation` is a variation key or `null`.
- A serve (a rule's `serve` and the flag's `fallthrough`) has exactly one of `variation` or `rollout`.
- The order of `variations`, `targets`, `rules`, `prerequisites`, clauses and weights is significant.

## 11. Loading a ruleset

An SDK MUST validate a ruleset when it loads it. An invalid ruleset is a `PARSE_ERROR`: the SDK MUST NOT
evaluate with it (it keeps its previous ruleset, or returns defaults). A loader checks:

- the top level is an object, `schema_version` is exactly the integer `1` (a runner MUST refuse any
  other version), and `project` and `environment` are strings;
- `flags` and `segments` are objects, and each flag's `key` equals its dictionary key;
- `type` is one of the four types, `enabled` is a boolean, and `version` is an integer (not a boolean);
- `variations` is non-empty, with unique string keys;
- `off_variation` is `null` or a key of the flag's variations, and every target, serve and weight
  variation is a key of the flag's variations;
- target `values` and segment `included` and `excluded` are lists of strings;
- a serve has exactly one of `variation` or `rollout`. A rollout has a string `bucket_by` and a non-empty
  list of weights, each an integer from 0 to 100000, that add up to exactly 100000;
- a rule has a non-empty list of clauses (`description` defaults to `""`); a clause has a string
  `attribute`, a known `operator`, a non-empty list of scalar `values` and a boolean `negate` (default
  `false`);
- a segment rule does not use `in_segment`.

A loader does **not** check cross-flag references: a missing prerequisite flag fails the prerequisite,
and a missing segment is never matched. It does not check for prerequisite cycles; the depth guard
handles them. It ignores unknown fields. A clause value that is valid JSON but unusable for its operator
(a bad regex, an invalid semver or date) is accepted and never matches.

## 12. Spec details (normative summary)

These rules restate edge cases fixed by the sections above.

1. **Null attributes.** An attribute whose value is JSON `null` counts as missing.
2. **Arrays.** An array attribute matches if any element matches. An empty array is present but matches
   nothing, so `negate` applies to it. An element that is an object or an array is the wrong type.
3. **`not_in`.** For one element, `not_in` is true when the element is a scalar that equals none of the
   values.
4. **`in_segment`.** It ignores `attribute` and the missing-attribute rule. `negate` applies. An unknown
   segment key is never matched.
5. **Targets.** Individual targets and segment `included`/`excluded` lists match only when the context
   `key` is a string.
6. **Bucketing value.** A string is used as is. An integral number (an integer, or a float with an
   integral value) with |n| ≤ 2^53−1 is written as base-10 text without a fraction. Anything else
   (a boolean, other numbers, objects, arrays, `null` or missing) counts as missing, which gives
   bucket 0.
7. **Prerequisites.** A prerequisite fails if its flag is missing, is disabled, evaluates with reason
   `ERROR`, or serves a variant other than the required one. A prerequisite whose own prerequisite fails,
   but whose off variation equals the required variation, passes.
8. **Depth guard.** `MAX_PREREQUISITE_DEPTH = 32`. Exceeding it makes the whole evaluation return the
   default with `ERROR`/`GENERAL`.
9. **Expected type.** `expected_type` is one of `boolean`, `string`, `number`, `json`. `json` accepts any
   value, and `number` accepts an integer or a fraction but not a boolean. The check runs only when a
   variation was served (`variant` not `null`). An error, or a missing off variation, returns the default
   unchecked.
10. **Non-object context.** If the context is not a JSON object, the default is returned with
    `ERROR`/`INVALID_CONTEXT`.
11. **Loading.** A loader validates the structure, the variation references inside each flag, and that
    weights add up to 100000. An unknown operator, or `in_segment` inside a segment rule, is a
    `PARSE_ERROR`. Cross-flag references are not checked: a missing prerequisite flag fails the
    prerequisite, and a missing segment is not matched. Unknown extra fields are ignored. Cycles are not
    checked, because the depth guard handles them.
12. **Unusable clause values.** A clause value that cannot be prepared never matches: a regex that does
    not compile, an invalid semver, an unparsable date or a wrong-typed value.
13. **Dates.** Both sides become integer milliseconds since the Unix epoch. RFC 3339 fractions are
    truncated to milliseconds, and numbers are floored. `before` means attribute < value, and `after`
    means attribute > value. Attribute strings must fully match the RFC 3339 pattern of section 6.6.
14. **Strict matching.** The SemVer and RFC 3339 patterns use ASCII semantics and must match the whole
    string, so a trailing newline or Unicode digits are refused.

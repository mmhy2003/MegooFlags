# MF-77: Evaluation engine spec, reference implementation and conformance suite

## Goal
Define exactly how MegooFlags evaluates a flag for a context, so every SDK behaves identically. This task delivers:
- a written spec and the ruleset JSON format that server SDKs will download;
- a stdlib-only Python reference engine that passes a language-neutral conformance suite;
- the API wiring: a ruleset builder, an in-memory ruleset cache and one management endpoint that evaluates flags for a context. The endpoint backs the future "Test this flag" panel.

## Decisions
- **Scope (your answer):** engine, builder, cache and one management `evaluate` endpoint. The SDK-key-protected `/sdk/*` and `/ofrep/*` endpoints stay in MF-78, which reuses the same engine and cache. Rejected: no endpoint, exposing the ruleset now, and an unauthenticated OFREP endpoint.
- **Bucketing hash (your answer):** SHA-256, with the first 8 bytes read as an unsigned big-endian integer, mod 100000. Rejected: MurmurHash3 and LaunchDarkly-style SHA-1.
  - **Correction to my question:** the hash input is `"<flag_key>.<bucket_value>"`, with **no rule-position salt**. Rule positions change when rules are reordered, and a salt based on them would move users to other variations. Without it, a user keeps the same bucket in every rule and the fallthrough of one flag. Raising a percentage only adds users; it never moves the users already in.
- **Engine location (your answer):** `api/src/megooflags_api/evaluation/`, stdlib only, with no DB, FastAPI or Pydantic imports. The Python SDK can copy or extract it later. Rejected: a standalone `spec/reference/` package.
- **Prerequisite failure (your answer):** the flag serves its off variation with the custom reason `PREREQUISITE_FAILED`, and `metadata.prerequisite` names the failing flag. Rejected: `DISABLED`.
- **Field naming:** the ruleset and the endpoint use snake_case, like the rest of the management API.
- **Missing bucketing attribute:** bucket 0, so the first variation with a non-zero weight is served. This is deterministic and keeps anonymous contexts working; returning an error would break every rollout for them.
- **Portable regexes:** `matches` uses the common subset of RE2 and Python syntax, with ASCII semantics. Validation when a rule is saved also refuses non-portable syntax: lookaround, backreferences, atomic groups and possessive quantifiers, and conditionals. The engine compiles patterns with `re.ASCII`.

## Evaluation spec (`spec/evaluation.md`)
**Context (T-1).** A JSON object of arbitrary attributes. The OpenFeature `targetingKey` maps to the attribute `key`.
- Attribute names refer to top-level keys only.
- A value is a scalar or an array of scalars. An array clause matches if any element matches.

**Order of evaluation.** The first step that applies decides the result.

| Step | What happens | Reason |
|---|---|---|
| 1 | Flag missing or archived: the caller's default is returned, error `FLAG_NOT_FOUND` | `ERROR` |
| 2 | Flag disabled: the off variation is served | `DISABLED` |
| 3 | Prerequisites, in order: each one is evaluated recursively with the same context. One fails if it is disabled, errors or serves a different variation. The off variation is served | `PREREQUISITE_FAILED` |
| 4 | Individual targets (T-2): the context `key` is in a target list | `TARGETING_MATCH` |
| 5 | Rules, in order: the first rule whose clauses all match (AND) is served, whether it serves a fixed variation or a rollout. `metadata.rule_index` is set | `TARGETING_MATCH` |
| 6 | Fallthrough with a rollout | `SPLIT` |
| 6 | Fixed fallthrough on a flag with no targets, rules or prerequisites | `STATIC` |
| 6 | Any other fixed fallthrough | `DEFAULT` |

**Off variation and type checks.**
- If the flag has no off variation, the caller's default is returned with the same reason and no variant.
- If the caller passes an expected type and the served value doesn't match it, the default is returned with error `TYPE_MISMATCH`.
- Error codes are OpenFeature's: `FLAG_NOT_FOUND`, `TYPE_MISMATCH`, `PARSE_ERROR`, `INVALID_CONTEXT` and `GENERAL`. `GENERAL` covers a prerequisite depth guard, which can only trip on a malformed ruleset.

**Clauses (T-3).**
- A clause matches if **any** of its values matches.
- If the attribute is missing, the clause is false and `negate` is not applied. Otherwise `negate` inverts the result.
- An attribute of the wrong type makes the match false, and `negate` still applies.

Operators:
- `in` / `not_in`: strict JSON equality. `1` equals `1.0`, `"1"` does not equal `1`, and booleans are compared as booleans.
- `contains`, `starts_with`, `ends_with`: strings only, case-sensitive.
- `matches`: an unanchored search with the portable regex subset.
- `lt`, `lte`, `gt`, `gte`: numbers only. Strings are never coerced.
- `semver_eq`, `semver_lt`, `semver_gt`: SemVer 2.0.0 precedence, with build metadata ignored. An invalid version is a non-match.
- `before`, `after`: the attribute is an RFC 3339 string or a number in Unix epoch milliseconds, and they are compared as instants.

**Segments (T-4).** `in_segment` matches if the context is in any of the listed segments:
- a context `key` in the segment's excluded list means the context is not in the segment;
- otherwise, a key in the included list means it is;
- otherwise, it is in the segment if any segment rule matches.

**Rollouts (T-5).**
- The bucketing value is the `bucket_by` attribute, `key` by default. It must be a string or an integer. Integers are written as base-10 text.
- The bucket is `sha256(utf8("<flag_key>.<value>"))[0:8]`, read as an unsigned big-endian integer, mod 100000.
- Walking the weights in their listed order, the first variation whose running total of weights is greater than the bucket is served.

## Ruleset format (`spec/ruleset.schema.json`, JSON Schema 2020-12)
```json
{ "schema_version": 1, "project": "shop", "environment": "production",
  "flags": { "<key>": { "key", "type", "enabled", "version",
      "variations": [{"key","value"}], "off_variation": "<key>|null",
      "prerequisites": [{"flag","variation"}],
      "targets": [{"variation","values":[...]}],
      "rules": [{"description","clauses":[{"attribute","operator","values","negate"}],
                 "serve": {"variation":"k"} | {"rollout":{"bucket_by","weights":[{"variation","weight"}]}}}],
      "fallthrough": <serve> } },
  "segments": { "<key>": { "included": [], "excluded": [], "rules": [{"clauses": [...]}] } } }
```
- Archived flags are left out.
- References use keys. For `in_segment`, `values` holds the segment keys.

## Architecture
- `evaluation/` (pure):
  - `model.py`: frozen dataclasses.
  - `parse.py`: `parse_ruleset(dict) -> Ruleset`. It validates the ruleset, precompiles regexes, semvers and dates, and raises `RulesetError` (`PARSE_ERROR`).
  - `operators.py`, `bucketing.py`.
  - `engine.py`: `evaluate(ruleset, flag_key, context, default, expected_type=None) -> Resolution(value, variant, reason, error_code, error_message, metadata)` and `evaluate_all(...)`.
- `services/ruleset.py`: `build_ruleset(session, environment) -> dict`. It uses a fixed number of queries whatever the number of flags (no N+1), reusing the loading patterns of `load_clauses`.
- `services/ruleset_cache.py`: an in-process cache on `app.state`, keyed by environment id and bounded LRU (256 environments).
  - Each lookup runs **one** fingerprint query: an md5 over the environment's `flag_environment (id, version)`, the project's non-archived `flag (id, updated_at)` and `segment (id, updated_at)`.
  - The ruleset is rebuilt and parsed only when the fingerprint changes. A per-environment `asyncio.Lock` stops concurrent rebuilds.
  - This is correct across API replicas. MF-78 can replace the per-request check with LISTEN/NOTIFY.
  - To make the fingerprint complete, variation create, update and delete also set `flag.updated_at`. Flag PATCH, archive and restore already change the flag row.
- `routers/evaluate.py`: the new endpoint, plus the stricter regex check in `services/clauses.py`.

## Interface
`POST /api/projects/{project}/environments/{env}/evaluate`

Request body: `{"context": {...}, "flags": ["k1", ...]}`. `flags` is optional; without it every non-archived flag is evaluated.

Response:
```json
{"results": [{"key", "value", "variant", "reason", "error_code", "error_message", "metadata"}]}
```
- Results are sorted by key.
- An unknown requested key returns a result with `FLAG_NOT_FOUND`.
- An unknown project or environment returns 404.
- `context` must be an object, or the request fails with 422.

`api/openapi.json` and `web/lib/api/schema.d.ts` are regenerated.

## Conformance suite (`spec/conformance/`)
- `cases/*.json` are grouped by topic: targets, each operator, negate and missing attributes, segments, prerequisites (pass, fail, chained, disabled), rollouts (weights, boundaries, missing attribute, custom `bucket_by`), reasons, type mismatch, not found and archived.
  - Each file is `{description, ruleset, tests: [{name, flag, context, default, expected_type?, expected: {value, variant, reason, error_code, metadata}}]}`.
- `bucketing.json` holds hash vectors: input string, then bucket. SDK authors can check their hash on its own.
- `conformance.schema.json` describes the case files. `README.md` explains how any SDK runs the suite: load each file, parse the ruleset, evaluate, then compare `value`, `variant`, `reason` and `error_code` exactly and `metadata` as a subset.
- `spec/README.md` is updated, and the suite is versioned with `schema_version`.

## Error handling and edge cases
- A prerequisite chain is evaluated recursively. Cycles are refused when a rule is saved, and the engine also has a depth guard that returns `GENERAL`.
- A pattern that is stored but can't be compiled is a clause non-match. It can't happen through the API.
- A JSON `null` variation value is a valid value, distinct from a missing off variation.
- Number type checks accept both int and float. Converting to int or float is left to each SDK.

## Testing
- `api/tests/test_conformance.py` runs every case in parallel against the reference engine, plus every bucketing vector. Done means 100 % of the cases pass.
- The fixtures and a built ruleset are validated against their JSON Schemas, using `jsonschema` as a new dev dependency.
- Unit tests cover operators, semver precedence, dates, regex portability and the bucket distribution: 100k keys land within ±1 % of the weights.
- Builder tests: a database config built through the API becomes the expected ruleset. A 10k-flag build uses a fixed number of statements.
- Cache tests: a hit runs a single statement, and every write (config PUT, toggle, kill, variation edit, segment PUT and delete, archive) invalidates the cache.
- Endpoint tests cover the 404 and 422 paths, unknown keys and all flags.
- A rough performance check: 10k evaluations of a rule-heavy flag in well under 1 s.
- One CHANGELOG `[Unreleased]` entry covers the evaluate endpoint and the evaluation spec and suite.

## Out of scope
- `/sdk/*` ruleset download, SSE, `/ofrep/*`, SDK keys and cross-replica push (MF-78).
- Any SDK, exposure events, the web "Test this flag" UI, nested or dotted attribute paths, and a version bump.

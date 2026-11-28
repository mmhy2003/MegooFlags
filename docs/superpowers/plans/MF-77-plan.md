# MF-77 implementation plan: evaluation spec, reference engine, conformance suite

**Goal:** define exactly how MegooFlags evaluates a flag, ship a stdlib-only Python reference engine that passes a language-neutral conformance suite with 100 % of cases passing, and add an API ruleset builder, an in-memory ruleset cache and one management `evaluate` endpoint.

**Architecture:** `api/src/megooflags_api/evaluation/` is a pure package (stdlib only). `parse_ruleset` turns the ruleset JSON into frozen dataclasses with prepared clause values, and `evaluate`/`evaluate_all` run the spec. The conformance fixtures and JSON Schemas live in `spec/`, and `api/tests/test_conformance.py` runs them against the engine. In the API, `services/ruleset.py` builds the ruleset dict from Postgres with a fixed number of queries. `services/ruleset_cache.py` caches the parsed ruleset per environment and checks a one-query fingerprint on each lookup. `routers/evaluate.py` exposes `POST /api/projects/{project}/environments/{env}/evaluate`.

Conventions that every step follows (from AGENTS.md and CLAUDE.md):
- Write the test first and watch it fail, then implement.
- Keep `ruff check`, `ruff format --check`, strict `mypy` (src only) and `pytest` clean.
- Commit each step on the current branch `megoo/MF-77-evaluation-engine-spec-reference-impleme`, with a message ending in the `Co-Authored-By` line. Never commit `.megoocolab/`.
- DB tests need Postgres and `MEGOOFLAGS_DATABASE_URL=postgresql+asyncpg://megooflags:megooflags@localhost:5432/megooflags` (AGENTS.md §9). Pure engine tests do not.
- Run all commands from `api/` unless stated otherwise.

## Spec details fixed by this plan
The design leaves a few edge cases open. This plan settles them as follows, and both `spec/evaluation.md` and the engine must say and do exactly this:
1. **Null attributes.** An attribute whose value is JSON `null` counts as missing.
2. **Arrays.** An array attribute matches if any element matches. An empty array is present but matches nothing, so `negate` applies to it. An element that is an object or an array is the wrong type.
3. **`not_in`.** For one element, `not_in` is true when the element is a scalar that equals none of the values.
4. **`in_segment`.** It ignores `attribute` and the missing-attribute rule. `negate` applies. An unknown segment key is never matched.
5. **Targets.** Individual targets and segment `included`/`excluded` lists match only when the context `key` is a string.
6. **Bucketing value.** A string is used as is. An integral number (int, or a float with an integral value) with |n| ≤ 2^53−1 is written as base-10 text without a fraction. Anything else (bool, other numbers, objects, arrays, null or missing) counts as missing, which gives bucket 0.
7. **Prerequisites.** A prerequisite fails if its flag is missing, is disabled, evaluates with reason `ERROR`, or serves a variant other than the required one. Note that a prerequisite whose own prerequisite fails, but whose off variation equals the required variation, passes. This is a literal reading of the design.
8. **Depth guard.** `MAX_PREREQUISITE_DEPTH = 32`. Exceeding it makes the whole evaluation return the default with `ERROR`/`GENERAL`.
9. **Expected type.** `expected_type` is one of `boolean`, `string`, `number`, `json`. `json` accepts any value, and `number` accepts int or float but not bool. The check runs only when a variation was served (variant not null). An error, or a missing off variation, returns the default unchecked.
10. **Non-object context.** If the context is not a JSON object, the default is returned with `ERROR`/`INVALID_CONTEXT`.
11. **What `parse_ruleset` checks.** It validates the structure, the intra-flag variation references and that the weights sum to 100000. An unknown operator or `in_segment` inside a segment rule raises `RulesetError`. Cross-flag references are not checked: a missing prerequisite flag fails the prerequisite, and a missing segment is not matched. Unknown extra fields are ignored. Cycles are not checked, because the depth guard handles them.
12. **Unusable clause values.** A clause value that can't be prepared never matches: a regex that doesn't compile, an invalid semver, an unparsable date or a wrong-typed value.
13. **Dates.** Both sides become integer **milliseconds** since the Unix epoch. RFC 3339 fractions are truncated to ms, and numbers are floored. `before` means attribute < value, and `after` means attribute > value. Attribute strings must match `RFC3339` (fullmatch).
14. **Strict matching.** `SEMVER` and `RFC3339` are compiled with `re.ASCII` and applied with `fullmatch`, so a trailing newline or Unicode digits are refused.

## File map
| File | Responsibility |
|---|---|
| `api/src/megooflags_api/evaluation/__init__.py` | Re-exports `Ruleset`, `Resolution`, `Reason`, `ErrorCode`, `RulesetError`, `parse_ruleset`, `evaluate`, `evaluate_all` |
| `evaluation/model.py` | Frozen dataclasses, `Reason`, `ErrorCode`, `FLAG_TYPES` (Appendix A) |
| `evaluation/bucketing.py` | `bucket_text`, `bucket`, `pick_variation` (Appendix B) |
| `evaluation/operators.py` | `SEMVER`, `RFC3339`, `OPERATORS`, `json_equal`, `parse_semver`, `semver_compare`, `to_millis`, `prepare`, `match_value`, `value_has_type` (Appendix C) |
| `evaluation/parse.py` | `RulesetError`, `parse_ruleset(dict) -> Ruleset` |
| `evaluation/engine.py` | `evaluate`, `evaluate_all` (Appendix D) |
| `spec/evaluation.md` | Written spec |
| `spec/ruleset.schema.json` | Ruleset JSON Schema 2020-12 (Appendix E) |
| `spec/conformance/README.md`, `conformance.schema.json`, `bucketing.json`, `cases/*.json` | Conformance suite (Appendix F) |
| `spec/README.md` | Replace "Nothing is here yet" with an index |
| `api/src/megooflags_api/services/clauses.py` | Import `SEMVER`/`RFC3339` from `evaluation.operators`, use fullmatch, add the regex portability check (Appendix I) |
| `api/src/megooflags_api/services/variations.py` | Variation create, update and delete also set `flag.updated_at` |
| `api/src/megooflags_api/services/ruleset.py` | `build_ruleset(session, environment) -> dict` (Appendix H) |
| `api/src/megooflags_api/services/ruleset_cache.py` | `RulesetCache` (Appendix G) |
| `api/src/megooflags_api/schemas/evaluation.py` | `EvaluateIn`, `EvaluationResult`, `EvaluateOut` |
| `api/src/megooflags_api/routers/evaluate.py` | The evaluate endpoint |
| `api/src/megooflags_api/main.py` | Sets `app.state.ruleset_cache` and includes the `evaluate` router |
| `api/pyproject.toml`, `api/uv.lock` | `jsonschema` dev dependency |
| `api/openapi.json`, `web/lib/api/schema.d.ts` | Regenerated |
| Tests | `api/tests/test_evaluation_bucketing.py`, `test_evaluation_operators.py`, `test_evaluation_parse.py`, `test_evaluation_engine.py`, `test_spec_schemas.py`, `test_conformance.py`, `test_ruleset_builder.py`, `test_ruleset_cache.py`, `test_evaluate.py`, plus additions to `test_clauses.py`, `test_variations.py`, `test_scale.py`, `test_openapi.py` |
| `CHANGELOG.md` | One `[Unreleased]` → Added entry |

## Steps

### 1. Engine model and bucketing
Create the `evaluation/` package with `model.py` (Appendix A) and `bucketing.py` (Appendix B). Its `__init__.py` re-exports the model names and grows in later steps.

Tests go in `api/tests/test_evaluation_bucketing.py`:
- `test_bucket_known_value`: `bucket("f", "u")` equals the value from `printf 'f.u' | sha256sum`, computed independently: take the first 16 hex characters, read them as an integer (`int(h, 16)`) and take mod 100000. Hard-code the number you get from the shell.
- `test_bucket_text`:
  - `"abc"` → `"abc"`;
  - `42` → `"42"`;
  - `42.0` → `"42"`;
  - `-0.0` → `"0"`;
  - `2**53-1` → its digits;
  - `2**53` → None;
  - `1.5` → None;
  - `True` → None;
  - `None` → None;
  - `[1]` → None.
- `test_pick_variation_boundaries`: with weights `[("a", 30000), ("b", 70000)]`, bucket 29999 picks `a`, bucket 30000 picks `b`, and bucket 0 with `[("z", 0), ("a", 100000)]` picks `a`.
- `test_distribution`: across 100 000 keys `f"user-{i}"` on flag `"rollout"`, with weights 25000/25000/50000, each count is within ±1000 of the expected count.
- `test_evaluation_is_stdlib_only`: use `ast` to read every `.py` file in the package. Every import root must be in `sys.stdlib_module_names`, or be `megooflags_api.evaluation` or a relative import.

Run `uv run pytest tests/test_evaluation_bucketing.py -q`. It fails with ModuleNotFoundError, then passes once the package exists.

Commit: `feat(eval): evaluation model and sticky bucketing (MF-77)`.

### 2. Clause operators
Implement `evaluation/operators.py` exactly as in Appendix C.

Tests go in `api/tests/test_evaluation_operators.py`, as parametrized tables:
- `json_equal`: `1` == `1.0`; `"1"` != `1`; `True` != `1`; `False` == `False`.
- Semver precedence, using the SemVer 2.0.0 §11 chain: `1.0.0-alpha < 1.0.0-alpha.1 < 1.0.0-alpha.beta < 1.0.0-beta < 1.0.0-beta.2 < 1.0.0-beta.11 < 1.0.0-rc.1 < 1.0.0`. Also `1.0.0+b1` equals `1.0.0+b2`, and `2.0.0 > 1.10.0 > 1.9.0`. Invalid inputs: `v1.0.0`, `1.0`, `01.0.0`, `"1.0.0\n"` and `"١.٢.٣"`.
- `to_millis`:
  - `"1970-01-01T00:00:01Z"` → 1000;
  - `"1970-01-01T01:00:00+01:00"` → 0;
  - `"1970-01-01t00:00:00.123456789z"` → 123;
  - `1500.9` → 1500;
  - `-0.5` → -1;
  - `"2026-01-01"` → None;
  - `True` → None;
  - `"2026-13-01T00:00:00Z"` → None.
- `match_value` for every operator, on both matching and non-matching elements:
  - `matches` is unanchored (`"b"` matches `"abc"`);
  - `\d` does not match `"٣"` because of ASCII semantics;
  - a prepared `None` (bad pattern) never matches;
  - `not_in` on a dict element is False;
  - `lt` with `"1"` is False.
- `value_has_type` for each type, and `bool` is not a `number`.

Run `uv run pytest tests/test_evaluation_operators.py -q`. It fails on the import, then passes.

Commit: `feat(eval): clause operators with portable semantics (MF-77)`.

### 3. Ruleset parser
Implement `evaluation/parse.py`:
- `class RulesetError(ValueError)`, with `error_code = ErrorCode.PARSE_ERROR`.
- `def parse_ruleset(data: Any) -> Ruleset`, which builds the Appendix A dataclasses from the Appendix E JSON.

Required checks. Each one raises `RulesetError` with a message that contains a JSON path such as `flags.a.rules[0].serve`:
- the top level is a dict and `schema_version == 1`;
- `project` and `environment` are strings;
- `flags` and `segments` are dicts;
- each flag `key` equals its dict key;
- `type` is in `FLAG_TYPES`, `enabled` is a bool, and `version` is an int and not a bool;
- `variations` is non-empty with unique string keys;
- `off_variation` is null or a known key;
- each target's `variation` is known and its `values` is a list of str;
- a serve has exactly one of `variation` (known) or `rollout`. A rollout needs `bucket_by` (str) and non-empty `weights` of `{variation known, weight int 0..100000}` that sum to 100000;
- a rule has `clauses`, a non-empty list. Its `description` defaults to `""`;
- a clause needs `attribute` (str), `operator` in `OPERATORS`, `values` (a non-empty list of scalars: str, int, float or bool) and `negate` (bool, default False);
- in a segment, `in_segment` inside its rules is an error.

Clause `prepared` is `operators.prepare(operator, values)`. Unknown fields are ignored. Prerequisite targets and segment keys are not checked.

Tests go in `api/tests/test_evaluation_parse.py`:
- a valid minimal ruleset parses;
- the flags keep their keys and the variations keep their order;
- one parametrized test per error above asserts `RulesetError` and that the path appears in the message;
- unknown fields are ignored;
- a bad regex parses, and its `prepared` is `(None,)`.

Run `uv run pytest tests/test_evaluation_parse.py -q` and watch it fail, then pass.

Commit: `feat(eval): ruleset parser and validation (MF-77)`.

### 4. Evaluation engine
Implement `evaluation/engine.py` as in Appendix D, and export it from `__init__.py`.

Tests go in `api/tests/test_evaluation_engine.py`. Use a small `ruleset(**flags)` helper that builds a dict and calls `parse_ruleset`. Cover:
- each step of the order table (Appendix D);
- the `STATIC` vs `DEFAULT` vs `SPLIT` reasons;
- a rule rollout gives `TARGETING_MATCH` with `rule_index`;
- `PREREQUISITE_FAILED` with `metadata.prerequisite` for a disabled, missing or wrong-variant prerequisite;
- a chained pass;
- a cycle gives `GENERAL`;
- a missing off variation returns the default with no variant;
- JSON `null` variation values;
- `TYPE_MISMATCH`, `INVALID_CONTEXT` and `FLAG_NOT_FOUND`;
- segment excluded, then included, then rules;
- `evaluate_all` sorts the keys, defaults to all flags and gives `FLAG_NOT_FOUND` for an unknown key.

Add `test_performance`: build a flag with 20 rules of 3 clauses each (`matches`, `semver_gt`, `in`) where only the last rule matches. 10 000 `evaluate` calls must take under 1.0 s, measured with `time.perf_counter`.

Run `uv run pytest tests/test_evaluation_engine.py -q` and watch it fail, then pass.

Commit: `feat(eval): reference evaluation engine (MF-77)`.

### 5. Written spec and ruleset JSON Schema
Write these files:
- `spec/evaluation.md`. Sections: Context; Order of evaluation (the design's table); Off variation and type checks; Error codes; Clauses and operators; Segments; Rollouts and bucketing (with a worked example that uses one `bucketing.json` vector); Ruleset format; Spec details 1–14 from this plan, worded as rules.
- `spec/ruleset.schema.json` (Appendix E).
- An updated `spec/README.md` that indexes `evaluation.md`, `ruleset.schema.json` and `conformance/`.

Add the dev dependency with `uv add --dev jsonschema`, which updates `pyproject.toml` and `uv.lock`.

Tests go in `api/tests/test_spec_schemas.py` and need `SPEC = Path(__file__).resolve().parents[2] / "spec"`:
- `Draft202012Validator.check_schema` passes on the ruleset schema;
- the minimal valid ruleset from step 3's tests validates;
- a ruleset with both `variation` and `rollout` in a serve fails;
- an unknown operator fails.

Run `uv run pytest tests/test_spec_schemas.py -q` and watch it fail, then pass.

Commit: `docs(spec): evaluation spec and ruleset schema (MF-77)`.

### 6. Conformance harness, schema, bucketing vectors and first cases
Add the following (Appendix F):
- `spec/conformance/conformance.schema.json`, `README.md` and `bucketing.json` with at least 20 vectors;
- `cases/targets.json`, `operators_equality.json`, `operators_string.json`, `operators_regex.json`, `operators_numeric.json`, `operators_semver.json`, `operators_date.json` and `clause_semantics.json`, with the cases listed in Appendix F.

Write `api/tests/test_conformance.py` as in Appendix F.

Run `uv run pytest tests/test_conformance.py -q`. Expect every case to pass, except the completeness test, which stays marked `xfail(strict=True)` until step 7.

Commit: `test(spec): conformance harness and operator cases (MF-77)`.

### 7. Remaining conformance cases
Add `cases/segments.json`, `prerequisites.json`, `rollouts.json`, `reasons.json` and `errors.json` (Appendix F), and remove the `xfail` from `test_suite_is_complete`.

Run `uv run pytest tests/test_conformance.py -q`. It must show 100 % passing, with no xfail or skip.

Commit: `test(spec): segments, prerequisites, rollouts and error cases (MF-77)`.

### 8. Portable regex validation on save
In `services/clauses.py`:
- import `SEMVER` and `RFC3339` from `megooflags_api.evaluation.operators`, delete the local copies, and use `fullmatch`;
- add `regex_portability_error` (Appendix I);
- `_value_error` for `matches` returns `invalid regular expression` if `re.compile(value, re.ASCII)` fails, else the portability message.

Add these cases to `tests/test_clauses.py` `CASES`, each `ok=False`:
- `("matches", ["a(?=b)"], False)`
- `("matches", ["(?<!a)b"], False)`
- `("matches", ["(a)\\1"], False)`
- `("matches", ["(?P<x>a)(?P=x)"], False)`
- `("matches", ["(?>a)"], False)`
- `("matches", ["a*+"], False)`
- `("matches", ["a{2}+"], False)`
- `("matches", ["(a)?(?(1)b|c)"], False)`
- `("semver_eq", ["1.2.3\n"], False)`

Add these, each `ok=True`:
- `("matches", ["[(?=]x"], True)`
- `("matches", ["\\(\\?=a"], True)`
- `("matches", ["(?P<x>a)b"], True)`
- `("matches", ["\\d+\\.\\d+"], True)`

Run `uv run pytest tests/test_clauses.py -q` and watch it fail, then pass. Also run `uv run mypy`.

Commit: `feat(api): refuse non-portable regexes in clauses (MF-77)`.

### 9. Variation writes touch the flag
In `services/variations.py`, add `def _touch(flag: Flag) -> None: flag.updated_at = func.now()` and call it in `add_variation` (before the flush), `update_variation` and `delete_variation`. The existing flushes write it.

Add `test_variation_writes_bump_flag_updated_at` to `tests/test_variations.py`, parametrized over add, patch and delete on a string flag. Read the flag's `updated_at` (GET flag) before and after, and assert it is greater after.

Run `uv run pytest tests/test_variations.py -q` and watch it fail, then pass.

Commit: `fix(api): variation changes update the flag timestamp (MF-77)`.

### 10. Ruleset builder
Implement `services/ruleset.py` `build_ruleset` (Appendix H).

Tests go in `tests/test_ruleset_builder.py`. Through the API on project `demo`:
- a string flag `color` with variations `red`/`blue`/`green`, off `red`;
- a boolean flag `gate`;
- segment `beta` with included `["u1"]`, excluded `["u2"]` and one rule;
- on `production`:
  - `gate` enabled;
  - `color` enabled, with target `blue: ["u3","u0"]`, a rule (`in_segment beta`) serving `green`, a rule (`semver_gt`) serving a rollout `bucket_by: org`, a fallthrough rollout and prerequisite `gate: true`;
- an archived flag `old`.

`build_ruleset(session, env)` must equal the expected dict exactly (archived flag absent, target values sorted, in_segment values the segment keys). It must validate against `spec/ruleset.schema.json` and parse with `parse_ruleset`. `staging` shows `color` disabled with fallthrough `{"variation":"red"}`.

Add to `tests/test_scale.py`: `test_build_ruleset_statement_count_is_constant`. The small `demo` project and the seeded 10k-flag `big` project issue the same statement count (≤ 14), and the big build has 10 000 flags.

Run `uv run pytest tests/test_ruleset_builder.py tests/test_scale.py -q` and watch it fail, then pass.

Commit: `feat(api): build the SDK ruleset from the database (MF-77)`.

### 11. In-memory ruleset cache
Implement `services/ruleset_cache.py` (Appendix G), and in `main.create_app` set `app.state.ruleset_cache = RulesetCache()`.

Tests go in `tests/test_ruleset_cache.py`:
- A miss builds and returns a `Ruleset`.
- A second `get` returns the same object (`is`) with exactly one statement, counted with `count_statements(app.state.engine)` around `cache.get`.
- Parametrized invalidation: after each write through the API, `get` returns a new object that reflects the change. The writes are: config PUT, toggle, kill, copy, variation add, edit and delete, flag PATCH `off_variation`, archive, restore, segment PUT, segment delete, and flag create.
- An LRU of size 2 evicts the oldest environment.
- 10 concurrent `get` calls on a cold cache (each with its own session) build once, measured with a counter monkeypatched around `build_ruleset` in `megooflags_api.services.ruleset_cache`.

Run `uv run pytest tests/test_ruleset_cache.py -q` and watch it fail, then pass.

Commit: `feat(api): per-environment ruleset cache with fingerprint check (MF-77)`.

### 12. Evaluate endpoint
Add `schemas/evaluation.py`, `routers/evaluate.py` and the router entry in `main.py` (Appendix J). Then regenerate:
- `uv run python -m megooflags_api.export_openapi openapi.json`
- `cd ../web && pnpm install --frozen-lockfile && pnpm gen:api`

Tests go in `tests/test_evaluate.py`:
- 404 for an unknown project or environment;
- 422 (`validation_error`) for `context` set to `[1]`, `"x"` or missing;
- all flags sorted with archived omitted;
- `flags: ["nope"]` gives `FLAG_NOT_FOUND` with value null;
- a targeting rule match returns `variant`, `reason: TARGETING_MATCH` and `metadata: {"rule_index": 0}`;
- the same context differs between `production` and `staging`;
- a disabled flag gives `DISABLED`.

Also add `"/api/projects/{project}/environments/{env}/evaluate"` to the path set in `tests/test_openapi.py`.

Run `uv run pytest tests/test_evaluate.py tests/test_openapi.py -q` and watch it fail, then pass.

Commit: `feat(api): evaluate flags for a context (MF-77)`.

### 13. CHANGELOG and full verification
Under `## [Unreleased]` → `### Added`, add one line:

> `POST /api/projects/{project}/environments/{env}/evaluate` evaluates flags for a context with the new reference evaluation engine. The evaluation spec, ruleset format and SDK conformance suite are published in `spec/`. Regular expressions in `matches` clauses must now use the portable RE2/Python subset: lookaround, backreferences, atomic groups, possessive quantifiers and conditionals are refused.

Then run everything in "Final verification" and fix anything that fails.

Commit: `docs: changelog for MF-77 (MF-77)`.

## Final verification
From the repository root:
```bash
python3 scripts/check_version.py
uv run --frozen --project api pytest scripts/tests
(cd api && uv sync --frozen && uv run ruff check . ../scripts && uv run ruff format --check . ../scripts && uv run mypy && uv run pytest)
(cd api && uv run pytest tests/test_conformance.py -v)   # 100 % pass, no skip/xfail
bash scripts/check_migrations.sh
(cd web && pnpm install --frozen-lockfile && pnpm lint && pnpm format:check && pnpm typecheck && pnpm build)
uv run --frozen --project api python -m megooflags_api.export_openapi api/openapi.json
pnpm --dir web gen:api
git diff --exit-code -- api/openapi.json web/lib/api/schema.d.ts
docker build -t megooflags-api:ci api && docker build -t megooflags-web:ci web && bash scripts/smoke.sh
```
No migration is added in this task.

---

## Appendix A: `evaluation/model.py`
```python
"""Parsed ruleset and evaluation results. Stdlib only."""
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

FLAG_TYPES = frozenset({"boolean", "string", "number", "json"})
MAX_PREREQUISITE_DEPTH = 32

class Reason(StrEnum):
    STATIC = "STATIC"; DEFAULT = "DEFAULT"; TARGETING_MATCH = "TARGETING_MATCH"; SPLIT = "SPLIT"
    DISABLED = "DISABLED"; PREREQUISITE_FAILED = "PREREQUISITE_FAILED"; ERROR = "ERROR"

class ErrorCode(StrEnum):
    FLAG_NOT_FOUND = "FLAG_NOT_FOUND"; TYPE_MISMATCH = "TYPE_MISMATCH"; PARSE_ERROR = "PARSE_ERROR"
    INVALID_CONTEXT = "INVALID_CONTEXT"; GENERAL = "GENERAL"

@dataclass(frozen=True, slots=True)
class Variation: key: str; value: Any
@dataclass(frozen=True, slots=True)
class Weight: variation: str; weight: int
@dataclass(frozen=True, slots=True)
class Rollout: bucket_by: str; weights: tuple[Weight, ...]
@dataclass(frozen=True, slots=True)
class Serve: variation: str | None; rollout: Rollout | None
@dataclass(frozen=True, slots=True)
class Clause:
    attribute: str; operator: str; values: tuple[Any, ...]; negate: bool
    prepared: tuple[Any, ...]          # operators.prepare(operator, values)
@dataclass(frozen=True, slots=True)
class Rule: description: str; clauses: tuple[Clause, ...]; serve: Serve
@dataclass(frozen=True, slots=True)
class Target: variation: str; values: frozenset[str]
@dataclass(frozen=True, slots=True)
class Prerequisite: flag: str; variation: str
@dataclass(frozen=True, slots=True)
class Flag:
    key: str; type: str; enabled: bool; version: int
    variations: dict[str, Variation]   # insertion order = ruleset order
    off_variation: str | None
    prerequisites: tuple[Prerequisite, ...]; targets: tuple[Target, ...]
    rules: tuple[Rule, ...]; fallthrough: Serve
@dataclass(frozen=True, slots=True)
class SegmentRule: clauses: tuple[Clause, ...]
@dataclass(frozen=True, slots=True)
class Segment: key: str; included: frozenset[str]; excluded: frozenset[str]; rules: tuple[SegmentRule, ...]
@dataclass(frozen=True, slots=True)
class Ruleset:
    schema_version: int; project: str; environment: str
    flags: dict[str, Flag]; segments: dict[str, Segment]
@dataclass(frozen=True, slots=True)
class Resolution:
    value: Any; variant: str | None; reason: str
    error_code: str | None = None; error_message: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
```
(Write one field per line in the real file. ruff format will do that.)

## Appendix B: `evaluation/bucketing.py`
```python
import hashlib
from collections.abc import Sequence
from typing import Any
from megooflags_api.evaluation.model import Weight

BUCKETS = 100_000
MAX_SAFE_INTEGER = 2**53 - 1

def bucket_text(value: Any) -> str | None:
    """The text hashed for `value`, or None when it can't be bucketed (counts as missing)."""
    if isinstance(value, bool):
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, int):
        return str(value) if abs(value) <= MAX_SAFE_INTEGER else None
    if isinstance(value, float) and value.is_integer() and abs(value) <= MAX_SAFE_INTEGER:
        return str(int(value))
    return None

def bucket(flag_key: str, text: str) -> int:
    digest = hashlib.sha256(f"{flag_key}.{text}".encode()).digest()
    return int.from_bytes(digest[:8], "big") % BUCKETS

def pick_variation(weights: Sequence[Weight], value: int) -> str:
    running = 0
    for w in weights:
        running += w.weight
        if running > value:
            return w.variation
    raise ValueError("weights do not add up to 100000")   # unreachable after parse_ruleset
```
For tests you can pass `Weight(...)` tuples. The rollout bucket is `bucket(flag.key, text)` when `bucket_text(context.get(rollout.bucket_by))` is not None, and `0` otherwise.

## Appendix C: `evaluation/operators.py`
```python
SEMVER = re.compile(<same pattern as services/clauses.py today>, re.ASCII)
RFC3339 = re.compile(r"^\d{4}-\d{2}-\d{2}[Tt ]\d{2}:\d{2}:\d{2}(\.\d+)?([Zz]|[+-]\d{2}:\d{2})$", re.ASCII)
OPERATORS = frozenset({"in","not_in","contains","starts_with","ends_with","matches","lt","lte","gt","gte",
                       "semver_eq","semver_lt","semver_gt","before","after","in_segment"})
SemverKey = tuple[int, int, int, tuple[tuple[int, int, str], ...] | None]

def is_number(v) -> bool        # int|float, not bool
def is_scalar(v) -> bool        # str, bool or number
def json_equal(a, b) -> bool    # bool only == bool; numbers numerically; str == str; else False
def parse_semver(v) -> SemverKey | None
    # str + SEMVER.fullmatch; prerelease ids -> (0, int(id), "") if id.isdigit() else (1, 0, id); none -> None
def semver_compare(a, b) -> int  # -1/0/1; core tuple first; None prerelease > any prerelease; else tuple compare
def to_millis(v) -> int | None
    # number (finite): math.floor(v); str: RFC3339.fullmatch, upper-case, ' '->'T', truncate fraction to 3 digits,
    # datetime.fromisoformat, (dt - EPOCH) // timedelta(milliseconds=1); ValueError -> None; else None
def prepare(operator, values) -> tuple[Any, ...]
    # matches: re.compile(v, re.ASCII) or None on re.error / non-str
    # semver_*: parse_semver(v); before/after: to_millis(v) if isinstance(v, str) else None
    # others: values unchanged
def match_value(operator, element, prepared) -> bool   # one scalar element of the attribute
    # in: any(json_equal(element, p)); not_in: is_scalar(element) and not any(json_equal(...))
    # contains/starts_with/ends_with: str element and any(str p and p in / startswith / endswith)
    # matches: str element and any(p is not None and p.search(element))
    # lt/lte/gt/gte: is_number(element) and any(is_number(p) and element OP p)
    # semver_eq/lt/gt: key = parse_semver(element); key and any(p and semver_compare(key, p) ==0/<0/>0)
    # before/after: t = to_millis(element); t is not None and any(p is not None and t < p / t > p)
    # in_segment or unknown: False (the engine handles in_segment)
def value_has_type(value, expected_type) -> bool   # ValueError if expected_type not in FLAG_TYPES
```

## Appendix D: `evaluation/engine.py`
```python
class _DepthExceeded(Exception): ...

def evaluate(ruleset, flag_key, context, default, expected_type=None) -> Resolution:
    if not isinstance(context, dict):
        return _error(default, ErrorCode.INVALID_CONTEXT, "context must be a JSON object")
    flag = ruleset.flags.get(flag_key)
    if flag is None:
        return _error(default, ErrorCode.FLAG_NOT_FOUND, f"flag '{flag_key}' not found")
    try:
        result = _evaluate_flag(ruleset, flag, context, default, 0)
    except _DepthExceeded:
        return _error(default, ErrorCode.GENERAL, "prerequisite chain is too deep or has a cycle")
    if expected_type is not None and result.variant is not None \
            and not value_has_type(result.value, expected_type):
        return _error(default, ErrorCode.TYPE_MISMATCH, f"flag '{flag_key}' is not a {expected_type}")
    return result

def evaluate_all(ruleset, context, flag_keys=None) -> dict[str, Resolution]:
    keys = sorted(set(ruleset.flags if flag_keys is None else flag_keys))
    return {key: evaluate(ruleset, key, context, None) for key in keys}

def _evaluate_flag(ruleset, flag, context, default, depth) -> Resolution:
    if not flag.enabled:                                         # step 2
        return _serve(flag, flag.off_variation, Reason.DISABLED, default)
    for prereq in flag.prerequisites:                            # step 3
        if not _prerequisite_met(ruleset, prereq, context, depth + 1):
            return _serve(flag, flag.off_variation, Reason.PREREQUISITE_FAILED, default,
                          {"prerequisite": prereq.flag})
    key = context.get("key")
    if isinstance(key, str):                                     # step 4
        for target in flag.targets:
            if key in target.values:
                return _serve(flag, target.variation, Reason.TARGETING_MATCH, default)
    for index, rule in enumerate(flag.rules):                    # step 5
        if all(_clause_matches(ruleset, c, context) for c in rule.clauses):
            return _serve_rule(flag, rule.serve, context, Reason.TARGETING_MATCH, default,
                               {"rule_index": index})
    ft = flag.fallthrough                                        # step 6
    if ft.rollout is not None: reason = Reason.SPLIT
    elif not (flag.targets or flag.rules or flag.prerequisites): reason = Reason.STATIC
    else: reason = Reason.DEFAULT
    return _serve_rule(flag, ft, context, reason, default, {})

def _prerequisite_met(ruleset, prereq, context, depth) -> bool:
    if depth > MAX_PREREQUISITE_DEPTH: raise _DepthExceeded
    pflag = ruleset.flags.get(prereq.flag)
    if pflag is None or not pflag.enabled: return False
    result = _evaluate_flag(ruleset, pflag, context, None, depth)
    return result.reason != Reason.ERROR and result.variant == prereq.variation

def _clause_matches(ruleset, clause, context) -> bool:
    if clause.operator == "in_segment":
        hit = any(_in_segment(ruleset.segments.get(k), ruleset, context) for k in clause.values)
        return hit != clause.negate
    value = context.get(clause.attribute)
    if value is None: return False                               # missing or null: negate not applied
    elements = value if isinstance(value, list) else [value]
    hit = any(match_value(clause.operator, e, clause.prepared) for e in elements)
    return hit != clause.negate

def _in_segment(segment, ruleset, context) -> bool:
    if segment is None: return False
    key = context.get("key")
    if isinstance(key, str):
        if key in segment.excluded: return False
        if key in segment.included: return True
    return any(all(_clause_matches(ruleset, c, context) for c in r.clauses) for r in segment.rules)

# _serve(flag, key, reason, default, metadata=None): key None -> Resolution(default, None, reason, metadata=...)
#   else Resolution(flag.variations[key].value, key, reason, metadata=dict(metadata or {}))
# _serve_rule: serve.variation -> _serve; rollout -> text = bucket_text(context.get(bucket_by));
#   b = 0 if text is None else bucket(flag.key, text); _serve(flag, pick_variation(weights, b), ...)
# _error(default, code, message) -> Resolution(default, None, Reason.ERROR, code, message, {})
```

## Appendix E: `spec/ruleset.schema.json`
Use JSON Schema 2020-12 with `$id` `https://megooflags.dev/spec/ruleset.schema.json`, top-level `additionalProperties: false`, and these definitions:
- Top level: required `schema_version` (`const: 1`), `project` (string), `environment` (string), `flags` (object of `#/$defs/flag`) and `segments` (object of `#/$defs/segment`).
- `key`: a string with `minLength: 1`.
- `scalar`: `{"type": ["string", "number", "boolean"]}`.
- `flag`: closed object. Required: `key`, `type` (enum of the 4 types), `enabled` (boolean), `version` (integer ≥ 1), `variations` (minItems 1, items `{key, value: true}` closed), `off_variation` (`["string", "null"]`), `prerequisites` (items `{flag, variation}` closed), `targets` (items `{variation, values: string[] minItems 1}` closed), `rules` (items `#/$defs/rule`), `fallthrough` (`#/$defs/serve`).
- `rule`: closed object. Required: `description` (string), `clauses` (minItems 1, items `#/$defs/clause`), `serve`.
- `clause`: closed object. Required: `attribute` (string minLength 1), `operator` (enum of the 16 operators), `values` (minItems 1, items `scalar`), `negate` (boolean).
- `serve`: `oneOf` either `{variation}` closed or `{rollout: #/$defs/rollout}` closed.
- `rollout`: closed object. Required: `bucket_by` (string minLength 1), `weights` (minItems 1, items `{variation, weight: integer 0..100000}` closed).
- `segment`: closed object. Required: `included` (string[]), `excluded` (string[]), `rules` (items: closed `{clauses}`).

## Appendix F: conformance suite
**Case file** (`cases/*.json`):
```json
{"schema_version": 1, "description": "...", "ruleset": {<ruleset, schema-valid>},
 "tests": [{"name": "unique-in-file", "flag": "k", "context": {...}, "default": <any>,
            "expected_type": "boolean|string|number|json (optional)",
            "expected": {"value": <any>, "variant": "k"|null, "reason": "...", "error_code": null|"...",
                         "metadata": {...}}}]}
```
- `conformance.schema.json` describes the case file shape above. It is closed, with `context`, `default` and `value` set to `true` (any), the `reason` enum (the 7 reasons) and the `error_code` enum (null plus the 5 codes).
- `bucketing.json` is `{"schema_version":1,"description":"...","vectors":[{"input":"<flag_key>.<value>","bucket":n}]}` with at least 20 vectors. Include: ASCII keys, an empty value (`"f."`), a non-ASCII value (`"f.é"`, `"f.日本"`), the integer forms `"f.42"` and `"f.0"`, and the `rollouts.json` keys.
  - Generate the buckets with `uv run python -c` and `bucketing.bucket`.
  - Cross-check at least 3 vectors with `printf '%s' 'f.u' | sha256sum` (first 16 hex characters, read as an integer, mod 100000) and note this in the README.
- `README.md` gives the runner algorithm:
  1. For each case file, parse `ruleset`. A parse error fails the file.
  2. For each test, evaluate `flag` with `context`, `default` and `expected_type`.
  3. Compare `value` (JSON equality, where `1 == 1.0` but `true != 1`), `variant`, `reason` and `error_code` exactly, and `metadata` as a subset.
  4. Hash each bucketing vector input and compare it to `bucket`.

  It also explains versioning: a runner must refuse an unknown `schema_version`.

**Required cases** (the test names are suggestions; each line is at least one test):
- `targets.json`:
  - a key in the list → its variation `TARGETING_MATCH`;
  - a key in no list → fallthrough `DEFAULT`;
  - targets win over a matching rule;
  - a non-string `key` (`42`) doesn't match a target `"42"`.
- `operators_equality.json`: `in` string, number with `1` vs `1.0`, `"1"` vs `1`, `true` vs `1`; `not_in` both ways; `not_in` on an array with one non-listed element matches.
- `operators_string.json`: `contains`, `starts_with` and `ends_with`, each with a hit, a miss and a case-sensitivity miss; a number attribute is a miss.
- `operators_regex.json`: an unanchored hit; `^...$` anchoring; `\d` with `"٣"` is a miss; an invalid stored pattern `"("` is a miss.
- `operators_numeric.json`: `lt`, `lte`, `gt` and `gte` at the boundaries; float vs int; a string `"5"` is a miss.
- `operators_semver.json`: the prerelease ordering; build metadata ignored in `semver_eq`; an invalid attribute `"v1.2.3"` is a miss.
- `operators_date.json`: `before` and `after` with a `Z` and an offset; an epoch-ms number attribute; a sub-ms fraction is truncated; an invalid date string is a miss.
- `clause_semantics.json`:
  - negate inverts;
  - a missing attribute with negate stays false;
  - a null attribute counts as missing;
  - a wrong type with negate is true;
  - an array with any element matching;
  - an empty array with negate is true;
  - AND across clauses;
  - OR across values;
  - first matching rule wins with `metadata.rule_index`.
- `segments.json`:
  - included;
  - excluded beats included;
  - excluded beats the rules;
  - a segment rule match;
  - no match;
  - any of several segments;
  - negated `in_segment`;
  - an unknown segment key is a miss;
  - a context without `key` falls through to the rules.
- `prerequisites.json`:
  - pass;
  - fail with the wrong variant (`PREREQUISITE_FAILED`, `metadata.prerequisite`);
  - prerequisite disabled;
  - prerequisite missing from the ruleset;
  - a chained pass (A→B→C);
  - a chained fail;
  - the first failing prerequisite is reported.
- `rollouts.json`:
  - fallthrough rollout `SPLIT`;
  - a rule rollout `TARGETING_MATCH` + `rule_index`;
  - boundaries: for a key with bucket b, weights `[b, 100000-b]` → second variation and `[b+1, 99999-b]` → first;
  - a missing bucketing attribute → first non-zero weight;
  - a custom `bucket_by: "org"`;
  - an integer bucketing value equals its string form;
  - a bool bucketing value counts as missing;
  - the same user lands in the same bucket in a rule and in the fallthrough.
- `reasons.json`:
  - `STATIC`;
  - `DEFAULT`;
  - `DISABLED`;
  - disabled with `off_variation: null` → default value, variant null, `DISABLED`;
  - a JSON-null variation value served with variant set.
- `errors.json`:
  - `FLAG_NOT_FOUND`;
  - an archived flag (absent from the ruleset) → `FLAG_NOT_FOUND`;
  - `TYPE_MISMATCH` with expected_type `boolean` on a string flag;
  - no mismatch for `json`;
  - `INVALID_CONTEXT` for context `"oops"`;
  - a cyclic prerequisite → `GENERAL`.

**`api/tests/test_conformance.py`:**
```python
SPEC = Path(__file__).resolve().parents[2] / "spec"
CONFORMANCE = SPEC / "conformance"
CASE_FILES = sorted((CONFORMANCE / "cases").glob("*.json"))
# load(path) via functools.cache; params = [pytest.param(path, t, id=f"{path.stem}::{t['name']}") ...]
# test_case(path, case): ruleset = parsed(path) (functools.cache); r = evaluate(ruleset, case["flag"],
#     case["context"], case["default"], case.get("expected_type")); compare with same_json(...) for value,
#     == for variant/reason/error_code, metadata items subset of r.metadata
# test_bucketing_vector(vector): split input at the first '.'; bucket(flag, rest) == vector["bucket"]
# test_case_files_match_schema / test_rulesets_match_schema (jsonschema Draft202012Validator)
# test_names_unique_per_file
# test_suite_is_complete: every operator in OPERATORS appears in some clause; every Reason appears and every
#     ErrorCode except PARSE_ERROR appears in expected
```
`same_json(a, b)`: both bool and equal, or both numbers (not bool) and equal, or both str and equal, or both None. Lists compare element by element and dicts key by key, recursively.

## Appendix G: `services/ruleset_cache.py`
```python
FINGERPRINT = text("""
SELECT md5(
  coalesce((SELECT string_agg(fe.id::text || ':' || fe.version::text, ',' ORDER BY fe.id)
            FROM flag_environment fe WHERE fe.environment_id = :environment_id), '')
  || '|' ||
  coalesce((SELECT string_agg(f.id::text || ':' || f.updated_at::text, ',' ORDER BY f.id)
            FROM flag f WHERE f.project_id = :project_id AND f.archived_at IS NULL), '')
  || '|' ||
  coalesce((SELECT string_agg(s.id::text || ':' || s.updated_at::text, ',' ORDER BY s.id)
            FROM segment s WHERE s.project_id = :project_id), ''))
""")

class RulesetCache:
    """Parsed rulesets per environment, rebuilt only when the fingerprint changes.

    Every lookup runs one fingerprint query, so the cache is correct across API replicas."""
    def __init__(self, max_environments: int = 256) -> None:
        self._max = max_environments
        self._entries: OrderedDict[uuid.UUID, tuple[str, Ruleset]] = OrderedDict()
        self._locks: dict[uuid.UUID, asyncio.Lock] = {}

    async def get(self, session: AsyncSession, environment: Environment) -> Ruleset:
        fingerprint = await session.scalar(FINGERPRINT, {"environment_id": environment.id,
                                                         "project_id": environment.project_id})
        hit = self._lookup(environment.id, fingerprint)
        if hit is not None: return hit
        lock = self._locks.setdefault(environment.id, asyncio.Lock())
        async with lock:
            hit = self._lookup(environment.id, fingerprint)   # another request rebuilt it
            if hit is not None: return hit
            ruleset = parse_ruleset(await build_ruleset(session, environment))
            self._entries[environment.id] = (fingerprint, ruleset)
            self._entries.move_to_end(environment.id)
            while len(self._entries) > self._max:
                evicted, _ = self._entries.popitem(last=False)
                self._locks.pop(evicted, None)
            return ruleset
    # _lookup: entry with equal fingerprint -> move_to_end and return the ruleset, else None
```
The fingerprint is read **before** the build, so a commit between the two only causes one extra rebuild later and never leaves a stale entry behind.

## Appendix H: `services/ruleset.py`
`async def build_ruleset(session: AsyncSession, environment: Environment) -> dict[str, Any]`. It uses a fixed set of statements whatever the number of flags. Every child query is filtered by a **join** on `FlagEnvironment.environment_id == environment.id` or `project_id`, never with large `IN` lists, so 10k flags stay below asyncpg's parameter limit. Each query runs even when the previous one returned no rows.
1. The project key (`select(Project.key).where(Project.id == environment.project_id)`).
2. The non-archived flags of the project (`id, key, type, off_variation_id`), ordered by key.
3. The variations of those flags (join flag, non-archived): `id, flag_id, key, value`, ordered by `flag_id, sort_order, key`.
4. The `flag_environment` rows of the environment for those flags: `id, flag_id, enabled, version, fallthrough_variation_id, fallthrough_bucket_by`.
5. Targets: `FlagTarget` joined to `FlagEnvironment` for the environment, ordered by `context_key`. Group them per variation, with groups in variation order.
6. Rules: `FlagRule` joined to FE for the environment, ordered by `flag_environment_id, position`.
7. Weights: `RolloutWeight` outer-joined to `FlagRule`, where the rule's FE or the weight's own FE is in the environment. Order them by variation order.
8. Flag-rule clauses: `Clause` joined to `FlagRule` and FE for the environment, ordered by `flag_rule_id, position`.
9. The segment keys of the flag-rule clauses: `ClauseSegment`, `Segment`, `Clause`, `FlagRule` and FE for the environment, ordered by `Segment.key`.
10. Prerequisites: `(fe.flag_id, Flag.key, Variation.key)` joined for the environment, ordered by position.
11. The project's segments, ordered by key.
12. Segment targets for the project (join segment), ordered by `context_key`, split into included and excluded.
13. Segment rules for the project, ordered by `segment_id, position`.
14. Segment-rule clauses (join `SegmentRule`, `Segment` on the project), ordered by `segment_rule_id, position`.

Output (Appendix E):
- **Flags:** keyed by key in key order, each `{key, type, enabled, version, variations:[{key,value}], off_variation, prerequisites:[{flag,variation}], targets:[{variation, values sorted}], rules:[{description, clauses, serve}], fallthrough}`.
- **Serve:** `{"variation": k}` or `{"rollout": {"bucket_by": bucket_by or "key", "weights": [...]}}`.
- **Clauses:** `{attribute, operator: operator.value, values, negate}`. For `in_segment`, `values` is the sorted segment keys.
- **Segments:** keyed by key, `{included, excluded, rules:[{clauses}]}`.

Plain Python types only (str, int, bool, list, dict), so the output is JSON-serializable.

## Appendix I: regex portability (`services/clauses.py`)
```python
NON_PORTABLE_GROUPS = (("(?=", "lookahead"), ("(?!", "lookahead"), ("(?<=", "lookbehind"),
                       ("(?<!", "lookbehind"), ("(?>", "atomic groups"), ("(?(", "conditionals"),
                       ("(?P=", "backreferences"))

def regex_portability_error(pattern: str) -> str | None:
    """Why `pattern` is outside the RE2/Python common subset, or None."""
    i, in_class = 0, False
    while i < len(pattern):
        c = pattern[i]
        if c == "\\":
            nxt = pattern[i + 1 : i + 2]
            if not in_class and nxt.isdigit() and nxt != "0":
                return "backreferences are not portable"
            i += 2
            continue
        if in_class:
            in_class = c != "]"
            i += 1
            continue
        if c == "[":
            in_class = True
            i += 1
            if pattern[i : i + 1] == "^": i += 1
            if pattern[i : i + 1] == "]": i += 1      # a leading ] is literal
            continue
        for prefix, what in NON_PORTABLE_GROUPS:
            if pattern.startswith(prefix, i):
                return f"{what} are not portable"
        if c in "*+?}" and pattern[i + 1 : i + 2] == "+":
            return "possessive quantifiers are not portable"
        i += 1
    return None
```
(Watch the `nxt.isdigit()` call: restrict it to ASCII digits with `nxt in "123456789"`.)

## Appendix J: endpoint
```python
# schemas/evaluation.py
class EvaluateIn(ApiModel):
    context: dict[str, JsonValue]
    flags: list[str] | None = Field(None, description="Flag keys to evaluate; every non-archived flag when omitted.")
class EvaluationResult(ApiModel):
    """An OpenFeature resolution detail. `value` is null when the flag is not found."""
    key: str; value: JsonValue; variant: str | None; reason: str
    error_code: str | None; error_message: str | None; metadata: dict[str, JsonValue]
class EvaluateOut(ApiModel):
    results: list[EvaluationResult]

# routers/evaluate.py
router = APIRouter(tags=["evaluation"], responses=ERROR_RESPONSES)

@router.post("/projects/{project}/environments/{env}/evaluate")
async def evaluate_flags(session: SessionDep, request: Request, project: str, env: str,
                         body: EvaluateIn) -> EvaluateOut:
    """Evaluate flags for a context exactly as SDKs do; results are sorted by key."""
    project_row = await get_project(session, project)
    env_row = await get_environment(session, project_row, env)
    cache: RulesetCache = request.app.state.ruleset_cache
    ruleset = await cache.get(session, env_row)
    results = evaluate_all(ruleset, body.context, body.flags)
    return EvaluateOut(results=[EvaluationResult(key=k, value=r.value, variant=r.variant, reason=r.reason,
        error_code=r.error_code, error_message=r.error_message, metadata=r.metadata) for k, r in results.items()])
```
In `main.py`, add `evaluate` to the router tuple and set `app.state.ruleset_cache = RulesetCache()` (done in step 11).

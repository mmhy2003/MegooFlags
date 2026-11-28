# Conformance suite

Language-neutral fixtures that every MegooFlags SDK must pass in CI. They check that an SDK evaluates
flags exactly as [`../evaluation.md`](../evaluation.md) says. The Python reference engine runs them in
[`api/tests/test_conformance.py`](../../api/tests/test_conformance.py).

| Path | What it is |
|---|---|
| [`conformance.schema.json`](conformance.schema.json) | JSON Schema (2020-12) of a case file |
| [`cases/*.json`](cases/) | Case files: one ruleset and the tests evaluated against it |
| [`bucketing.json`](bucketing.json) | Bucketing hash vectors |

## Case files

```json
{
  "schema_version": 1,
  "description": "What the file covers",
  "ruleset": { "...": "a ruleset valid against ../ruleset.schema.json" },
  "tests": [
    {
      "name": "unique-within-the-file",
      "flag": "flag-key",
      "context": { "key": "user-1" },
      "default": false,
      "expected_type": "boolean",
      "expected": {
        "value": true,
        "variant": "on",
        "reason": "TARGETING_MATCH",
        "error_code": null,
        "metadata": { "rule_index": 0 }
      }
    }
  ]
}
```

`context` and `default` can be any JSON value (a non-object context is a test of `INVALID_CONTEXT`).
`expected_type` is optional and is one of `boolean`, `string`, `number` or `json`.

## Runner algorithm

1. For each file in `cases/`, check its `schema_version` (see Versioning) and parse `ruleset` with the
   SDK's ruleset parser. A parse error fails every test in the file.
2. For each test, evaluate `flag` with `context`, `default` and, when present, `expected_type`.
3. Compare the result with `expected`:
   - `value` with JSON equality: numbers compare numerically (`1 == 1.0`), but a boolean only equals a
     boolean (`true != 1`) and a string only equals a string (`"1" != 1`). Arrays compare element by
     element and objects key by key, with the same rules;
   - `variant`, `reason` and `error_code` exactly (`null` is a value too);
   - `metadata` as a subset: every key in `expected.metadata` must be in the result's metadata with a
     JSON-equal value. The result may carry more keys.
4. For each vector in `bucketing.json`, split `input` at the **first** `.` into the flag key and the
   bucketing value (`"f.a.b"` is flag `f` with value `a.b`), compute the bucket and compare it with
   `bucket`.

Report each test as `<file stem>::<name>`, for example `targets::key-in-list`.

## Versioning

Every file carries `schema_version`. This is version `1`. A runner must **refuse** a file with any other
`schema_version` instead of guessing at its meaning; a new version is published when the file format or
the evaluation semantics change.

## Bucketing vectors

The buckets in `bucketing.json` were generated with the reference engine and cross-checked with the
shell, independently of any MegooFlags code:

```sh
h=$(printf '%s' 'f.u' | sha256sum | cut -c1-16)
python3 -c "print(int('$h', 16) % 100000)"   # 68258
```

The vectors `f.u`, `f.`, `f.日本`, `f.42` and `rollout.user-1` were checked this way. The vectors cover
ASCII values, an empty value, non-ASCII values, the integer forms `42` and `0`, a value with a `.` in it,
and the keys used by `cases/rollouts.json`.

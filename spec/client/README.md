# Client SDK cases

Language-neutral cases that every MegooFlags client SDK (browser and mobile, which evaluate remotely) must
pass in CI. They check the two things a client SDK computes itself: how it turns stored evaluate results
into OpenFeature resolution details, and how it turns an OpenFeature context into the evaluate request
context, its canonical JSON and its hash. The rules behind them are in
[`../delivery.md` section 11](../delivery.md#11-client-sdks).

[`api/tests/test_spec_client.py`](../../api/tests/test_spec_client.py) validates these files: the schema,
every stored result against the server's `SdkEvaluationResult`, and every `body`, `canonical` and
`context_hash` against a reference implementation, so the cases stay true to the server.

| Path | What it is |
|---|---|
| [`client.schema.json`](client.schema.json) | JSON Schema (2020-12) of both case files |
| [`resolution.json`](resolution.json) | Stored results mapped to resolution details |
| [`context.json`](context.json) | OpenFeature contexts mapped to the request context, canonical JSON and hash |

## Resolution cases

```json
{
  "schema_version": 1,
  "kind": "resolution",
  "description": "What the file covers",
  "tests": [
    {
      "name": "boolean-served-from-network",
      "results": [
        {"key": "f", "value": true, "variant": "on", "reason": "TARGETING_MATCH",
         "error_code": null, "error_message": null}
      ],
      "source": "network",
      "flag": "f",
      "type": "boolean",
      "default": false,
      "expected": {"value": true, "variant": "on", "reason": "TARGETING_MATCH", "error_code": null}
    }
  ]
}
```

- `results` is the list of stored evaluate results (exactly the fields of a `POST /sdk/v1/evaluate` result
  for a client key, so no `metadata`), or `null` when the SDK has no results at all.
- `source` is where the stored results came from: `network` or `cache`.
- `type` is the OpenFeature read: `boolean`, `string`, `integer`, `number` or `object`.

## Context cases

```json
{
  "schema_version": 1,
  "kind": "context",
  "description": "What the file covers",
  "tests": [
    {
      "name": "targeting-key-becomes-key",
      "context": {"targetingKey": "user-1", "plan": "pro"},
      "body": {"key": "user-1", "plan": "pro"},
      "canonical": "{\"key\":\"user-1\",\"plan\":\"pro\"}",
      "context_hash": "<64 lowercase hex characters>"
    }
  ]
}
```

- `context` is an OpenFeature evaluation context written as JSON: `targetingKey` is the targeting key,
  every other member is an attribute, and `{"$date": "<RFC 3339>"}` stands for a date value.
- `body` is the `context` sent to `POST /sdk/v1/evaluate`; `canonical` its canonical JSON; `context_hash`
  the hash the cache entry carries.

## Runner algorithm

1. For each file, check its `schema_version` (see Versioning) and its `kind`.
2. **Resolution:** for each test, build the SDK's store from `results` (none when `null`) with `source`,
   resolve `flag` with the read for `type` and `default`, and compare the result with `expected`:
   - `value` with JSON equality, as in [`../conformance/README.md`](../conformance/README.md) step 3:
     numbers compare numerically (`3 == 3.0`), a boolean only equals a boolean and a string only a string;
   - `variant`, `reason` and `error_code` exactly (`null` is a value too).
3. **Context:** for each test, build the SDK's `EvaluationContext` from `context`, map it to the request
   context, and compare it with `body` (JSON equality); compare its canonical JSON with `canonical` and its
   hash with `context_hash` exactly.

Report each test as `<file stem>::<name>`, for example `resolution::flag-missing-is-flag-not-found`.

### Integer reads

- `integer` tests use the SDK's integer read and `number` tests its double or number read.
- JavaScript has no integer read: the JS runner skips `integer` tests and reports them as not applicable.
  They count neither as passed nor as failed.
- Kotlin has two integer reads: its runner runs every `integer` test twice, through
  `getIntegerEvaluation` and through `getLongEvaluation`.

## Mapping rules

- **Results.** No results at all → the default, reason `ERROR`, `PROVIDER_NOT_READY`. Flag missing →
  `FLAG_NOT_FOUND`. A stored `error_code` → the default, reason `ERROR`, that code (an unknown code is
  `GENERAL`). `variant: null` without an error → the default with the stored reason, no variant and no
  error. A value of the wrong type → `TYPE_MISMATCH` (an integer read needs an integral number within the
  integer type's range). Otherwise the stored value and variant, with reason `CACHED` when `source` is
  `cache` and the stored reason otherwise. Every error returns the default, `variant: null` and reason
  `ERROR`.
- **Context.** Every attribute is copied. A non-empty targeting key is written as `key`, replacing a `key`
  attribute; an empty one is treated as absent. A date becomes UTC RFC 3339 with exactly three fraction
  digits and `Z` (`2026-11-10T09:15:02.000Z`). Integral numbers become integers.

## Canonical JSON

The canonical JSON of a value is what the cache's `context_hash` is computed from, so every SDK must
produce it byte for byte:

- no whitespace;
- object keys sorted by Unicode code point (not by UTF-16 code unit: `"～"` (U+FF5E) sorts before `"😀"`
  (U+1F600));
- strings escaped as JSON with only `\"`, `\\`, `\b`, `\f`, `\n`, `\r`, `\t` and the other characters
  U+0000 to U+001F as `\u00xx` (lowercase hex); every other character is written raw as UTF-8;
- integral numbers whose magnitude is below 2^53 as integers (`3.0` is `3`); other numbers in the
  language's shortest round-trip form. Such numbers may hash differently between SDKs, which costs only a
  cache miss, so the cases do not use them.

`context_hash` is the lowercase hex SHA-256 of the UTF-8 bytes of the canonical JSON.

## Required tests

`resolution.json` must keep at least these tests:

- each type served from the network: `boolean-served-from-network`, `string-served-from-network`,
  `integer-served-from-network`, `number-served-from-network`, `object-served-from-network`;
- each type served from the cache (reason `CACHED`): `boolean-served-from-cache`,
  `string-served-from-cache`, `integer-served-from-cache`, `number-served-from-cache`,
  `object-served-from-cache`;
- `prerequisite-failed-passed-through`;
- `null-variant-without-error-returns-default`;
- stored error codes: `stored-type-mismatch`, `stored-parse-error`, `stored-invalid-context`,
  `stored-general`, and the same from the cache: `stored-type-mismatch-from-cache`,
  `stored-parse-error-from-cache`, `stored-invalid-context-from-cache`, `stored-general-from-cache`;
- `unknown-stored-error-code-is-general`;
- wrong types: `boolean-read-of-string-is-type-mismatch`, `string-read-of-number-is-type-mismatch`,
  `number-read-of-boolean-is-type-mismatch`, `object-read-of-string-is-type-mismatch`,
  `integer-read-of-fraction-is-type-mismatch` (`1.5`), `integer-read-of-out-of-range-is-type-mismatch`
  (`1e20`);
- integral numbers: `integer-read-of-3-served`, `integer-read-of-3.0-served`,
  `number-read-of-integer-served`;
- `object-read-of-object-served`;
- `flag-missing-is-flag-not-found`;
- `no-results-is-provider-not-ready`.

Arrays as object values are not in the shared cases: some SDKs' object types cannot hold them.

`context.json` must keep at least these tests: `targeting-key-becomes-key`,
`targeting-key-replaces-key-attribute`, `no-targeting-key-keeps-key-attribute`,
`empty-targeting-key-is-absent`, `nested-objects-and-lists`, `booleans-null-and-numbers`,
`date-becomes-utc-milliseconds`, `input-key-order-does-not-matter`, `keys-sorted-by-code-point` and
`string-escapes` (`"`, `\`, newline, tab, U+0001 and non-ASCII characters).

## Versioning

Every file carries `schema_version`. This is version `1`. A runner must **refuse** a file with any other
`schema_version` instead of guessing at its meaning; a new version is published when the file format or
the mapping rules change.

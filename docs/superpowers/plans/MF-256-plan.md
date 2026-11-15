# MF-256 implementation plan: Node.js, Python, Java, .NET, Ruby and PHP server SDKs as OpenFeature providers

**Goal:** ship six server SDKs (`sdks/node`, `sdks/python`, `sdks/java`, `sdks/dotnet`, `sdks/ruby`, `sdks/php`, each 0.1.0) that are OpenFeature providers, evaluate the downloaded ruleset in-process (p99 under 1 ms on 10 000 flags), follow changes over SSE with a polling fallback, start offline from a bootstrap file or the cache, send counts and deduplicated exposures in the background, and pass 100 % of `spec/conformance` plus an end-to-end test in CI.

**Architecture:** every SDK is a port of `sdks/go` with the same internal layers: a pure `engine` (ruleset loader, operators, portable regex, bucketing, evaluation), `transport` (headers, status mapping, backoff), `delivery` (store, disk cache, SSE streamer, poller, sync coordinator), `events` (aggregator and batcher) and the public provider. Node.js, Java, .NET, Ruby and PHP share one regex translator design (section B2); Python uses `re.ASCII`. PHP has no long-lived process, so it replaces the in-process sync with shared cache files, request upkeep and an optional daemon (section L6).

## How to read this plan
- **B1 to B10** are the blueprint: language-neutral contracts copied from `sdks/go` (the file to read when in doubt is named in each section). **L1 to L6** are the per-language rules: file map, tooling, OpenFeature API, differences. A step says "B2 for Java": implement B2 following L3.
- Blueprint names are written in camelCase. Use the language's convention for the same words (`parseRfc3339Millis` is `parse_rfc3339_millis` in Python and Ruby, `ParseRfc3339Millis` in .NET).
- The step limit is 50, so steps are larger than in MF-83 (one SDK layer per step). Each step still leaves the build green and is one commit.
- Order: steps 1 and 2 are shared; then Node.js (3 to 10), Python (11 to 16), Java (17 to 24), .NET (25 to 32), Ruby (33 to 40), PHP (41 to 48); then the release jobs (49) and the release and agent docs (50). An SDK's last step must be green (unit, conformance, p99, e2e, CI jobs) before the next SDK starts.

## Conventions for every step
- Branch `megoo/MF-256-server-sdks-as-openfeature-providers-nod`; the PR goes into `develop`. One commit per step with the subject given in the step, ending with the `Co-Authored-By` trailer the session requires. Never commit `.megoocolab/`, build outputs, dependency directories or caches. Never create or push tags, never publish a package.
- TDD: write the failing test, run it and see it fail (a compile or import error counts for a new symbol), implement, run it again.
- Runtime dependencies: the language's OpenFeature SDK only (plus what that SDK itself brings). Test and build tools are development dependencies.
- No change to `api/`, `web/`, `spec/*.md` or `sdks/go` (except `ci.yml` reusing the composite action in step 2).
- The SDK key is never logged and never put in a URL. Log and error messages never contain it.
- Internal modules are not part of the public surface: one provider class, one config type, one version constant per SDK.
- Third-party versions in L1 to L6 were looked up on 2026-11-14. Use a newer compatible release when one exists; when a stated version does not exist, take the latest release that meets the constraint written next to it, and say so in the commit body.
- If a toolchain is not installed locally, run the same commands in its official image, for example `docker run --rm -v "$PWD":/repo -w /repo/sdks/java maven:3.9-eclipse-temurin-21 mvn -B -ntp verify` (`node:22`, `mcr.microsoft.com/dotnet/sdk:8.0`, `ruby:3.4`, `php:8.3-cli` with `composer:2`).
- After a workflow change run `docker run --rm -v "$PWD:/repo" -w /repo rhysd/actionlint:latest`; it must print nothing.

## Design notes (how the plan reads the design; no design change)
1. **Who emits READY.** The Node.js, Python, Java and .NET OpenFeature SDKs emit `PROVIDER_READY` themselves when initialisation returns, so the provider emits READY only later (first ruleset after an init timeout, reconnect after STALE), as the Go SDK does. The Ruby SDK 0.8 expects the provider to emit READY or ERROR inside `init` (the non-deprecated path).
2. **Java format check** runs on JDK 17 and later only (Spotless 3 needs 17); the JDK 11 CI leg compiles, lints and tests.
3. **Ruby logger.** `logger` is a bundled gem from Ruby 4.0 and cannot be required under Bundler without declaring it. The SDK tries `require "logger"` and falls back to a small internal stderr logger, so the gem declares no extra dependency.
4. **PHP load order.** A request uses the compiled cache when it exists (it is what the network refresh writes), otherwise the bootstrap file, otherwise a synchronous fetch. PHP has no provider events, so e2e check 2 has no configuration-changed event.
5. **Regex subset edges** not spelled out in the design are decided by "common to RE2 and Python `re`": see B2 (for example `{,m}`, repeat counts over 1000 and `[:` inside a class are refused).
6. **32-bit integers.** Java `getIntegerEvaluation` and .NET `ResolveIntegerValueAsync` return 32-bit integers: an integral number outside that range is `TYPE_MISMATCH`.
7. **p99 guard** runs alone (its own command and CI step), not inside the parallel unit run, so CPU contention does not make it flaky.
8. **Node.js** `engines` is `>=22`; `require()` of the ESM package needs 22.12, which the README says.

## File map (shared)
| File | Change |
|---|---|
| `spec/conformance/cases/operators_regex.json` | 15 flags and 27 tests (step 1) |
| `.github/actions/sdk-e2e-stack/action.yml` | New composite action: build images, start the stack, mint the token (step 2) |
| `scripts/sdk_e2e_token.sh`, `scripts/tests/test_sdk_e2e_token.py` | New: mint the API token for e2e tests (step 2) |
| `.github/workflows/ci.yml` | `sdk-go-e2e` uses the action (step 2); jobs `sdk-<lang>` and `sdk-<lang>-e2e` (last step of each SDK) |
| `scripts/check_sdk_version.py`, `scripts/tests/test_check_sdk_version.py` | New: tag against SDK manifest (step 49) |
| `.github/workflows/release.yml` | `sdk-<lang>/v*` triggers and six release jobs (step 49) |
| `sdks/README.md` | Planned order and rows (step 2); one status row per SDK |
| `sdks/RELEASING.md` | New (step 50) |
| `AGENTS.md` | §1 (step 2), §9 (step 50) |
| `CHANGELOG.md` | One `[Unreleased]` "Added" line per SDK |

The per-SDK file maps are in L1 to L6.

## Blueprint

### B1. JSON values and engine primitives (Go: `internal/engine/value.go`, `bucketing.go`, `semver.go`, `date.go`)
**JSON tree.** null, boolean, number, string, array, object. A boolean is never a number. Where the language has integers (Python, Java, .NET, Ruby, PHP) an integer literal that fits 64 bits stays an integer and two integers compare exactly; every other comparison is on doubles. In JavaScript every number is a double.

| Function | Contract |
|---|---|
| `isNumber(v)`, `isScalar(v)` | number and not boolean; string, boolean or number |
| `jsonEqual(a, b)` | boolean only equals boolean; numbers numerically (`1 == 1.0`); string equals string; anything else (null included) is false |
| `bucketText(v)` | string as is; integral number with abs(n) <= 2^53-1 as base-10 text without fraction or exponent (`42.0` gives `42`, `-0.0` gives `0`); anything else: none |
| `bucket(flagKey, text)` | first 8 bytes of `sha256(utf8(flagKey + "." + text))` as an unsigned big-endian integer, mod 100000. Portable form: `r = 0; for each of the 8 bytes: r = (r * 256 + byte) % 100000` |
| `pickVariation(weights, bucket)` | first variation whose running total is greater than the bucket |
| `parseSemver(s)` | strict SemVer 2.0.0 over the whole string, ASCII only. Cut the build at the first `+` (each build identifier non-empty `[0-9A-Za-z-]`), then the prerelease at the first `-` (each identifier non-empty `[0-9A-Za-z-]`; an all-digit identifier has no leading zero), then exactly three core parts, each `0` or `[1-9][0-9]*`. Keep the parts as digit strings |
| `compareSemver(a, b)` | core parts compare by (length, then text); no prerelease is greater than any prerelease; identifiers left to right: numeric below alphanumeric, numeric by (length, text), alphanumeric by ASCII text; a shorter list is smaller. Build is ignored |
| `parseRfc3339Millis(s)` | the whole string must be `YYYY-MM-DD` + one of `T t space` + `hh:mm:ss` + optional `.` and one or more digits + `Z`, `z` or `+hh:mm` / `-hh:mm`, ASCII digits only. Valid when year >= 1, month 1 to 12, day valid for the month (Gregorian leap years), hour <= 23, minute <= 59, second <= 59, and offset hours*60+minutes < 1440. Fraction truncated to 3 digits. Result: `(daysFromCivil(y, m, d) * 86400 + h * 3600 + mi * 60 + s - offsetMinutes * 60) * 1000 + millis`, computed with integer arithmetic, not the host date library |
| `toMillis(v)` | string: `parseRfc3339Millis`; number: floor (non-finite is invalid); anything else invalid |

**Tests to write (vectors).**
- `jsonEqual`: (1, 1.0) true; (true, 1) false; ("1", 1) false; (null, null) false; (false, false) true; ("a", "a") true.
- `bucketText`: "u" gives "u"; 42 and 42.0 give "42"; -0.0 gives "0"; 42.5, true, null, [1], 9007199254740992 give none.
- `bucket("f", "u")` is 68258; `pickVariation` with a 30000, b 40000, c 30000: 29999 gives a, 30000 gives b, 68258 gives b, 99999 gives c; weights a 0, b 100000 with bucket 0 gives b.
- Semver valid: `1.2.3`, `1.0.0-alpha.1`, `1.0.0+build.5`, `1.0.0-0.3.7`, `1.0.0-x-y-z.--`, `1.0.0-alpha+b.1`. Invalid: `v1.2.3`, `1.2`, `1.2.3.4`, `01.2.3`, `1.2.3-01`, `1.2.3-`, `1.2.3+`, `1.2.3-a..b`, `1.2.3` followed by a newline, a leading space, an Arabic-Indic digit, `1.2.3-α`. Order: `1.0.0-alpha < 1.0.0-alpha.1 < 1.0.0-alpha.beta < 1.0.0-beta < 1.0.0-beta.2 < 1.0.0-beta.11 < 1.0.0-rc.1 < 1.0.0`; `1.0.0+b1` equals `1.0.0+b2`; `99999999999999999999.0.0 > 9.0.0`.
- Dates: `1970-01-01T00:00:00Z` is 0; `1970-01-01T00:00:00.123456789Z` is 123; `.5Z` is 500; `1970-01-01T01:00:00+01:00` is 0; `1969-12-31T23:59:59Z` is -1000; `2026-01-01t00:00:00z` and `2026-01-01 00:00:00Z` are 1767225600000; `2024-02-29T00:00:00Z` valid; `+23:59` valid. Invalid: `2026-01-01`, `2026-02-30T00:00:00Z`, `2026-01-01T24:00:00Z`, `2026-01-01T00:00:60Z`, no zone, `+24:00`, year `0000`, `2026-01-01T00:00:00.Z`, a trailing newline, non-ASCII digits. `toMillis`: 1500.9 is 1500; -0.5 is -1; NaN, infinity and true are invalid.

### B2. Portable regex translator (Node.js, Java, .NET, Ruby, PHP; design section "Portable regex translator")
API: `compilePortable(pattern)` returns a matcher or a compile error with a reason; `matcher.test(subject)` is an unanchored search returning a boolean. The same three stages in every port.

**Stage 1: parse** (recursive descent over code points; flags `i`, `m`, `s` are lexically scoped).
```
pattern     := ("(?" [ims]+ ")")*  alternation        -- global flags only at offset 0 (one or more groups in a row)
alternation := concat ("|" concat)*                    -- empty alternatives are allowed
concat      := (atom quantifier?)*
atom        := "(" alternation ")" | "(?:" alternation ")" | "(?P<" name ">" alternation ")"
             | "(?" [ims]+ ":" alternation ")" | class | "." | "^" | "$" | escape | literal
quantifier  := ("*" | "+" | "?" | "{n}" | "{n,}" | "{n,m}") "?"?      -- the optional "?" makes it lazy
```
Rules:
- `name` is `[A-Za-z_][A-Za-z0-9_]*`; a duplicate name is an error.
- `{` starts a quantifier only when the text is `{digits}`, `{digits,}` or `{digits,digits}`. `{,digits}` is an error. n > m, or n or m over 1000, is an error. Any other `{` and every `}` is a literal.
- A quantifier with nothing to repeat is an error: at the start of the pattern, a group or an alternative; after `^ $ \A \b \B`; after another quantifier (`a**`, `a+*`, `a{2}{3}`, `a*+`, `a++`, `a?+`, `a*??`).
- Escapes outside a class: `\d \D \w \W \s \S`; `\A \b \B`; `\t \n \r \f \v \a` (9, 10, 13, 12, 11, 7); `\xHH` (exactly two hex digits); backslash followed by any ASCII character that is not a letter or digit (that literal). Every other escape is an error (`\1` to `\9`, `\0`, `\z`, `\Z`, `\G`, `\p`, `\P`, `\Q`, `\E`, `\u`, `\U`, `\N`, `\x{…}`, `\e`, `\R`, `\K`, backslash before a non-ASCII character, a trailing backslash).
- Class `[...]`: optional leading `^`; a `]` right after `[` or `[^` is a literal; members are literals, the escapes `\t \n \r \f \v \a \xHH`, backslash + ASCII non-alphanumeric, the shorthands `\d \D \w \W \s \S`, and ranges `x-y` (x and y single characters or single-character escapes, x <= y, else error; a shorthand as an end point is an error). A `-` that cannot form a range (first, last, right before `]`) is a literal. `[` inside a class is a literal, but `[:` inside a class is an error (POSIX classes differ between engines). `\b`, `\A`, `\B` and any other letter escape inside a class is an error. An unterminated class is an error.
- Any `(?` form not in the grammar is an error: `(?=`, `(?!`, `(?<=`, `(?<!`, `(?<name>`, `(?P=name)`, `(?>`, `(?(`, `(?#`, `(?-i)`, `(?i-m:`, any flag other than `i m s`, global flags after offset 0. Unbalanced parentheses are an error.

**Stage 2: AST.** Nodes: `Set(ranges)`, `Concat`, `Alt`, `Group`, `Repeat(node, min, max or unbounded, lazy)`, `Assert(kind)` with kinds `START`, `END_DOLLAR`, `LINE_START`, `LINE_END`, `WORD_B`, `NOT_WORD_B`. A `Set` is a sorted, merged list of inclusive code point ranges inside 0 to 0x10FFFF; it is always positive (negation is resolved by complementing).
- literal c: `{c}`; under `i`, an ASCII letter becomes `{c, c XOR 0x20}`. Non-ASCII letters never fold.
- `.`: everything except 0x0A; under `s`: everything.
- `\d` = 0x30-0x39; `\w` = `0-9 A-Z _ a-z`; `\s` = 0x09, 0x0A, 0x0B, 0x0C, 0x0D, 0x20; `\D \W \S` = complements.
- class: union of its members; then under `i` add, for every ASCII letter in the set, its other case; then complement if negated.
- `^` is `START`, or `LINE_START` under `m`; `$` is `END_DOLLAR`, or `LINE_END` under `m`; `\A` is `START`; `\b` is `WORD_B`; `\B` is `NOT_WORD_B`.
- every group is a plain group (names dropped; capturing is not needed).

**Stage 3: emit.** Only these constructs are written; nothing depends on host defaults and no host flag is used. `W` is `[0-9A-Za-z_]`, `LF` is the code point escape of 0x0A.

| | Node.js `new RegExp(src, "u")` | Java `Pattern.compile(src)` | .NET `new Regex(src, RegexOptions.CultureInvariant)` | Ruby `Regexp.new(src)` | PHP `'~(*UTF)' . src . '~'` |
|---|---|---|---|---|---|
| code point | `\u{H}` | `\x{H}` | `\uHHHH` (BMP) | `\u{H}` | `\x{H}` |
| absolute start `S` | `(?<![^])` | `\A` | `\A` | `\A` | `\A` |
| absolute end `E` | `(?![^])` | `\z` | `\z` | `\z` | `\z` |

- `Set`: `[` then each range as `cp` or `cp-cp` then `]`. Every code point is written as a hex escape (no raw punctuation, so no host class syntax can interfere). An empty set is `(?!)`. A set with a single code point may be written as the bare escape.
- `START` = `S`; `END_DOLLAR` = `(?=LF?E)`; `LINE_START` = `(?:S|(?<=LF))`; `LINE_END` = `(?=LF|E)`.
- `WORD_B` = `(?:(?<=W)(?!W)|(?<!W)(?=W))`; `NOT_WORD_B` = `(?:(?<=W)(?=W)|(?<!W)(?!W))`.
- `Group` = `(?:…)`; `Alt` joins with `|`; `Repeat` = operand then `*`, `+`, `?`, `{n}`, `{n,}` or `{n,m}`, then `?` when lazy.
- **Ruby and PHP:** remove U+D800 to U+DFFF from every set before writing it (their strings cannot hold surrogates). A subject that is not valid UTF-8 never matches: Ruby checks `valid_encoding?` on a UTF-8 string and rescues encoding errors; PHP requires `preg_match('//u', $subject) === 1` first.
- **.NET** matches UTF-16 code units, so a set is written as `(?:A|B|H|L)`, leaving out empty parts: `A` = the astral ranges as surrogate pair classes (for a range a to b with high and low surrogates ha, la, hb, lb: if ha == hb, `[ha][la-lb]`; otherwise `[ha][la-\uDFFF]` when la is not DC00, `[hb][\uDC00-lb]` when lb is not DFFF, and `[x-y][\uDC00-\uDFFF]` for the full high surrogates between); `B` = the BMP ranges without D800 to DFFF as one class; `H` = `[highs in the set](?![\uDC00-\uDFFF])`; `L` = `(?<![\uD800-\uDBFF])[lows in the set]`. So a pair is one character and a lone surrogate is one character.
- A host compile error on the emitted source is returned as a compile error (it should never happen; a test compiles every vector).

**Tests to write.** (a) The vector table below, each row as `compilePortable(p).test(subject) == expected`. (b) The refused list: each pattern returns a compile error. (c) Emitter checks against the language's column for four patterns: `^a$`, `(?m)^a$`, `\bx`, `(?i)[a-c]`. (d) In Ruby and PHP: a subject with invalid UTF-8 returns false for the pattern `a`.

Vector table (Python `re` with `re.ASCII` semantics; `LF` = U+000A, `CR` = U+000D, `VT` = U+000B):

| Pattern | Subject | Match |
|---|---|---|
| `b` | `abc` / `xyz` | yes / no |
| `^ab$` | `ab` / `abc` / `xab` / `ab`+LF / `ab`+LF+LF / `ab`+CR | yes / no / no / yes / no / no |
| `\d` | `a3` / U+0663 | yes / no |
| `(?i)k` | `K` / U+212A | yes / no |
| `(?i)^[s]$` | `S` / U+017F | yes / no |
| `(?i)^[j-l]$` | `K` / U+212A | yes / no |
| `(?i)é` | `é` / `É` | yes / no |
| `(?i)[^a]` | `A` / `b` | no / yes |
| `^(?i:ab)c$` | `ABc` / `ABC` | yes / no |
| `(?im)^B$` | `a`+LF+`b` | yes |
| `^\s$` | VT / space / U+00A0 / U+0085 / U+2028 | yes / yes / no / no / no |
| `^\S$` | VT / U+00A0 | no / yes |
| `^[\s]$` | VT | yes |
| `^[^\d]$` | `x` / `5` | yes / no |
| `^[\w-]$` | `-` / `a` / `é` | yes / yes / no |
| `^\w+$` | `a_1` / `é` | yes / no |
| `a\bé` | `aé` | yes |
| `a\Bé` | `aé` | no |
| `\bfoo\b` | `a foo b` / `afoo` | yes / no |
| `^.$` | CR / LF / U+2028 / U+1F600 | yes / no / yes / yes |
| `^..$` | U+1F600 | no |
| `^[😀a]$` | U+1F600 / `a` | yes / yes |
| `^a.b$` | `a`+LF+`b` | no |
| `(?s)^a.b$` | `a`+LF+`b` | yes |
| `^b$` | `a`+LF+`b`+LF+`c` | no |
| `(?m)^b$` | `a`+LF+`b`+LF+`c` | yes |
| `(?m)a$` | `a`+LF+`b` / `a`+CR+LF+`b` | yes / no |
| `(?m)^$` | `a`+LF+LF+`b` | yes |
| `(?m)\Ab` | `b`+LF+`a` / `a`+LF+`b` | yes / no |
| `^$` | empty / LF | yes / yes |
| empty pattern | empty / `x` | yes / yes |
| `^(?P<x>ab)+c$` | `ababc` | yes |
| `^(?:a\|b\|)$` | empty / `c` | yes / no |
| `^a{2,3}$` | `aa` / `aaaa` | yes / no |
| `^a{2,}$` | `aaaaa` | yes |
| `^a*?$` | `aaa` | yes |
| `x{` | `x{` | yes |
| `x{a}` | `x{a}` | yes |
| `^\x41\t\.$` | `A`+TAB+`.` | yes |
| `^[a[]$` | `[` / `a` / `b` | yes / yes / no |
| `^[a&&b]$` | `&` | yes |
| `^[a\-z]$` | `-` / `b` | yes / no |
| `^[a-]$` | `-` | yes |
| `^[]a]$` | `]` | yes |
| `^[^]a]$` | `b` / `]` | yes / no |
| `^[a-c-e]$` | `-` / `d` | yes / no |
| `^a\|b$` (escaped bar) | `a\|b` | yes |

Refused list (each is a compile error): `(?=a)`, `(?!a)`, `(?<=a)b`, `(?<!a)b`, `(a)\1`, `(?P<n>a)(?P=n)`, `(?P<n>a)(?P<n>b)`, `(?>a)`, `(?(1)a|b)`, `(?<n>a)`, `(?#c)a`, `a*+`, `a++`, `a?+`, `a**`, `a{2}{3}`, `*a`, `(*a)`, `a|*`, `^*`, `\b+`, `a{,2}`, `a{3,2}`, `a{1001}`, `\z`, `\Z`, `\pL`, `\x{41}`, `A`, `\q`, `\0`, a single backslash, `(?x)a`, `(?U)a`, `(?a)a`, `a(?i)b`, `(?-i:a)`, `[[:alpha:]]`, `[a-\d]`, `[\w-a]`, `[z-a]`, `[\b]`, `[]`, `[a`, `(`, `)`, `(a`.

### B3. Ruleset loader and evaluation (Go: `parse.go`, `evaluate.go`; reference: `api/src/megooflags_api/evaluation/parse.py`, `engine.py`, `operators.py`; spec: `evaluation.md` §3 to §11)
**Model.** `Ruleset{project, environment, flags, segments, warnings}`; `Flag{key, type, enabled, version, variations (ordered, by key), offVariation or none, prerequisites[{flag, variation}], targets[{variation, values set}], rules[{description, clauses, serve}], fallthrough serve}`; `Serve` = `{variation}` or `{rollout{bucketBy, weights[{variation, weight}]}}`; `Segment{included set, excluded set, rules: list of clause lists}`; `Clause{attribute, operator, negate, values, prepared}`; `Variation{key, value, isInt, intValue}` (`isInt` when the value is a number with an integral value that fits 64 bits).

**Loader.** `parseRuleset(textOrTree)` returns a Ruleset or raises a parse error whose message is `<json path>: <reason>` (for example `flags.f.rules[0].clauses[1].operator: unknown operator`). Invalid JSON is a parse error with path `ruleset`. It checks exactly evaluation.md §11:
- root is an object; `schema_version` is required and is the integer 1 (not a boolean; not `1.0` where the language can tell); `project` and `environment` are strings; `flags` and `segments` are objects;
- flag: object; `key` equals its dictionary key; `type` is one of boolean, string, number, json; `variations` is a non-empty list of objects with a unique string `key` and a required `value` (any JSON, null included); `off_variation` is required and is null or a known key; `prerequisites`, `targets`, `rules` are required lists; `enabled` is a boolean; `version` is an integer and not a boolean; `fallthrough` is a serve;
- prerequisite: strings `flag` and `variation`; target: known `variation`, `values` a list of strings; rule: optional string `description` (default empty), non-empty `clauses`, `serve`;
- serve: object with exactly one of `variation` (known) or `rollout`; rollout: string `bucket_by`, non-empty `weights`, each with a known `variation` and an integer `weight` from 0 to 100000 (not a boolean), adding up to exactly 100000;
- clause: string `attribute`; `operator` one of `in not_in contains starts_with ends_with matches lt lte gt gte semver_eq semver_lt semver_gt before after in_segment`; `values` a non-empty list of scalars; optional boolean `negate` (default false); `in_segment` is refused in a segment rule;
- segment: `included` and `excluded` lists of strings; `rules` a list of objects with `clauses`;
- unknown fields are ignored; cross-flag references and cycles are not checked.

**Prepared values** (a value the operator cannot use is left out, so it never matches): `in`, `not_in`: all values; `contains`, `starts_with`, `ends_with`: strings; `matches`: compiled patterns of the string values (a pattern that does not compile adds `"<path>: regex does not compile: <reason>"` to `warnings`); `lt lte gt gte`: numbers; `semver_*`: parsed versions; `before`, `after`: milliseconds of valid RFC 3339 strings (a number value never matches); `in_segment`: the string values.

**Evaluate.** `evaluate(ruleset, flagKey, context, default, expectedType or none)` returns `Result{value, variant or none, reason, errorCode or none, errorMessage, ruleIndex or none, prerequisite or none}`; `metadata()` is `{rule_index}` and/or `{prerequisite}` when set, else empty. It is pure and takes no lock.
1. Context is not a JSON object: default, `ERROR`, `INVALID_CONTEXT`.
2. Flag missing: default, `ERROR`, `FLAG_NOT_FOUND`.
3. Flag disabled: off variation, `DISABLED`.
4. Prerequisites in order; each is met when its flag exists, is enabled and, evaluated with the same context and a null default, has a reason other than `ERROR` and serves the required variant. Checking a prerequisite at depth over 32 makes the whole evaluation default, `ERROR`, `GENERAL`. The first one not met serves the off variation with `PREREQUISITE_FAILED` and `prerequisite` set.
5. Targets, only when the context `key` is a string: first target whose values contain it, `TARGETING_MATCH`.
6. Rules in order: first rule whose clauses all match, `TARGETING_MATCH`, `ruleIndex` set.
7. Fallthrough: `SPLIT` for a rollout; `STATIC` when the flag has no targets, rules or prerequisites; otherwise `DEFAULT`.
- Off variation missing: the default, the same reason, no variant, no error.
- Rollout: `bucketText(context[bucketBy])`; none gives bucket 0.
- Expected type is checked only when a variation was served: `boolean`, `string`, `number` (not a boolean), `json` (anything, null included); a mismatch is default, `ERROR`, `TYPE_MISMATCH`.
- Clause: `in_segment` is "context is in any listed segment" XOR negate (attribute ignored). Otherwise an attribute that is missing or null is false without negate; an array matches when any element matches; then XOR negate. An element of the wrong type (objects and arrays included) does not match.
- Operators on one element: `in` equals any value; `not_in` is a scalar equal to none; `contains`/`starts_with`/`ends_with` on strings, by code point; `matches` on strings; `lt lte gt gte` on numbers; `semver_eq/lt/gt` on valid version strings; `before`/`after` compare `toMillis(element)` with the prepared milliseconds.
- Segment: a string `key` in `excluded` is out; in `included` is in; otherwise in when any segment rule matches.

**Tests to write.** Loader: a valid ruleset; one error case per bullet above, asserting the path in the message; unknown fields ignored; `negate` default; prepared values per operator; the regex warning. Evaluate: disabled (with and without an off variation); flag not found; invalid context (array, string, null); targets (a number key does not match); rule index; clause negate (missing attribute, wrong type, empty array); one case per operator; segments (excluded before included, unknown segment, context without key); rollout (bucket by another attribute, missing value gives bucket 0); prerequisite failed; a prerequisite serving its off variation that equals the required one passes; depth guard (a chain of 33 fails, 32 passes); a cycle gives `GENERAL`; static and default reasons; type mismatch; a null variation value served for `json`.

### B4. Conformance runner and p99 guard (Go: `conformance_test.go`, `internal/engine/bench_test.go`; `spec/conformance/README.md`)
**Runner.** Reads `../../spec/conformance/cases/*.json` (sorted) and `bucketing.json`, relative to the SDK directory. For each file: refuse any `schema_version` other than the integer 1; parse `ruleset` with the SDK's loader (a parse error fails every test of the file); for each test evaluate `flag` with `context`, `default` and `expected_type` when present; compare `value` with deep JSON equality (`1 == 1.0`, `true != 1`, `"1" != 1`, arrays element by element, objects key by key, `{}` is not `[]`), `variant`, `reason` and `error_code` exactly (null included), and `metadata` as a subset. Each test is reported as `<file stem>::<name>`. Each bucketing vector splits `input` at the first `.` and is reported as `bucketing::<input>`. Also test: at least one case file and at least 20 vectors exist; a temp file with `schema_version` 2 is refused (and `1.0` where the language can tell); the JSON equality helper on the 9 pairs of `api/tests/test_conformance.py::test_same_json`. 100 % must pass.

**p99 guard.** Port `genRuleset`: 10 000 flags `flag-0` to `flag-9999` and 50 segments, all flags enabled, version 1, off variation `off`.
- Even i: type boolean; variations on true, off false, alt false; one target serving `alt` with the 100 keys `user-<(i + k*37) % 1000>` for k 0 to 99; rules: `plan` in `plan-0` to `plan-19` serves on; `email` matches `^user-[0-9]+$` serves off; `app_version` semver_gt `1.5.0` serves alt; fallthrough rollout by `key` with weights on 33334, off 33333, alt 33333.
- Odd i: type string; variations on "on", off "off", alt "alt"; no targets; rules: `key` in_segment `segment-<i % 50>` serves alt; `signed_up` before `2025-01-01T00:00:00Z` serves off; fallthrough variation on.
- i > 0 and i % 10 == 0: one prerequisite `{flag: flag-<i-1>, variation: on}`.
- Segment `segment-<s>`: included `user-<s*1000 + k>` for k 0 to 999, excluded empty, one rule `org` in `org-<s>`.
- 100 contexts, j 0 to 99: key `user-<j*503>`, plan `plan-<j % 40>`, email `user-<j>` when j % 3 == 0 else `user<j>@example.com`, app_version `1.<j % 10>.0`, org `org-<j % 100>`.
Test: load the ruleset, run 10 000 warm-up evaluations, then time 100 000 evaluations one by one with a monotonic high-resolution clock, picking the flag and the context with a seeded PRNG; print p50, p99 and max; fail when p99 is 1 ms or more. It runs through its own command, alone.

### B5. Transport (Go: `internal/transport`)
- Headers on every request: `Authorization: Bearer <key>`, `User-Agent: megooflags-<lang>/<version>`, `Accept: application/json`; `Content-Type: application/json` with a body. The stream request sends `Accept: text/event-stream` and `Cache-Control: no-cache`. URLs are `<baseUrl>/sdk/v1/ruleset`, `/sdk/v1/stream`, `/sdk/v1/events`; the base URL has its trailing slashes removed.
- `statusError(status, headers, now)`: 2xx and 304 none; 401 and 403 `Fatal(status)`; 429 `RetryAfter(seconds)`; any other status `Http(status)`. `Fatal` also exists with reason `revoked`. Message of Fatal: `megooflags: SDK key refused (<status or revoked>)`.
- `parseRetryAfter(value, now)`: digits are seconds (a negative number is 0); an HTTP-date gives max(date - now, 0); missing or invalid gives none.
- `Backoff{base, max, rand}`: the first `next()` is min(base, max); the n-th is `min(max, base * 2^n) * (0.8 + 0.2 * rand())`; `reset()` starts over; `base` can be replaced (the server's `retry:`).
- Timeouts: poll 10 s, events POST 10 s, stream none (dead-connection timer instead). A cancellable sleep.

**Tests to write.** Headers (bearer, user agent, content type only with a body, key not in the URL); the status table (200, 204, 304, 401, 403, 429 with and without Retry-After, 500, 404); Retry-After `5`, `-1`, `abc`, a future and a past HTTP-date, missing; backoff with rand = 1 and base 3 s, max 60 s gives 3, 6, 12, 24, 48, 60, 60; rand = 0 gives 0.8 times from the second wait; never above max; reset; sleep ends early when cancelled.

### B6. Delivery (Go: `internal/delivery`)
**Store.** Holds the current snapshot `{ruleset, etag, source}` (source `bootstrap`, `cache`, `stream` or `poll`); `load()`; `swap(new)` returns the old one. Readers take no lock.

**Cache.** File `<cacheDir>/megooflags-<first 16 hex of sha256(utf8(sdkKey))>.json`, one JSON object `{"etag": "<string>", "ruleset": <the ruleset object>}`. `save(rawRuleset, etag)` creates the directory (mode 0700), writes a temp file in the same directory, sets mode 0600 and renames it over the cache file. `load()` returns `(ruleset, etag)`, "not found" for a missing file (silent), and an error for unreadable or corrupt content (logged as a warning, then ignored). No cache directory: load is "not found" and save does nothing. The file is the Go SDK's format, so a directory can be shared.

**SSE reader.** `readSse(byteStream, onLine, onRetry, onEvent)`. Lines end with LF or CRLF; split on the LF byte before decoding UTF-8. Every line (comments included) calls `onLine`. A line starting with `:` is a comment. `field:value` with one optional space after the colon; a line without a colon is a field with an empty value. Fields: `event`, `data` (several lines are joined with LF), `id` (ignored when it contains NUL), `retry` (digits only, milliseconds, calls `onRetry`); other fields are ignored. An empty line dispatches `{name (default "message"), data, id}` when at least one `data` line was seen, then resets. An unterminated last event is discarded. A line of any length is accepted.

**Streamer** (`GET /sdk/v1/stream`). Loop until stopped or fatal:
1. Connect, sending `Last-Event-ID` when the store has an ETag. A non-200 status goes through `statusError`.
2. On 200 call `onConnected`, start the dead timer (45 s, reset on every line), and read events: `put` calls `onPut(data, id)` (a false result is logged and the stream continues); `revoked` is `Fatal(revoked)`; other names are ignored; `retry` replaces the backoff base.
3. When the connection ends: Fatal stops the loop and is reported. Otherwise, if it had been connected for 60 s or more, reset the backoff and the failure count; then `failures += 1`, `wait = backoff.next()` (for a 429: `max(retryAfter, wait)`), call `onDisconnected(error, failures)`, sleep `wait` (cancellable) and reconnect. Backoff base 3 s, max 60 s.

**Poller** (`GET /sdk/v1/ruleset`). One `poll()`: send `If-None-Match` when the ETag is not empty; 304 is success; 200 calls `onRuleset(body, ETag header)` and an invalid ruleset is an error; other statuses go through `statusError`; timeout 10 s.

**Sync.**
- `apply(raw, etag, source)`: `parseRuleset`; invalid: log a warning, keep the previous snapshot, return the error. Valid: swap the store, save the cache when the source is `stream` or `poll` (a save error is only logged), log each ruleset warning once, call `onChange(old, new)`.
- `start()`: the streamer, or with `disableStream` only the poll loop.
- `onConnected`: stop the poll loop, status `Ready`. `onDisconnected(err, failures)`: status `Stale`; when failures >= 2 start the poll loop.
- Poll loop: polls are at least `pollInterval` apart, also across restarts of the loop (keep `nextPoll`); a 429 sets `nextPoll = started + max(retryAfter, pollInterval)`; Fatal stops everything. With `disableStream` each poll reports `Stale` on error and `Ready` on success.
- Fatal (from the stream or a poll): stop the streamer and the poller, report status `Fatal(error)` exactly once.
- `stop()`: cancels and waits for the workers; idempotent; safe before `start()`. No callback runs after `stop()` or after Fatal.
- Test seam: an internal tuning object (dead timeout, stable-after, retry base and max, poll-after-failures, unclamped poll interval) that only tests set.

**Tests to write.** Store: load before swap; swap returns the old snapshot. Cache: round trip; missing file; corrupt file; the file name is 16 hex characters and does not contain the key; mode 0600; atomic replace leaves no temp file; creates the directory; no cache directory; the file parses as `{"etag", "ruleset"}`. SSE: named event with id; default name; multi-line data; comments call onLine only; valid and invalid retry; CRLF; id with NUL; unterminated event; unknown field; field without colon; only one leading space removed; a 1 MiB data line; a multi-byte character split across two chunks. Streamer: put calls onPut and onConnected; headers (bearer, Accept, no key in the URL); Last-Event-ID; pings keep the stream alive; dead connection reconnects; unknown events ignored; invalid put continues; revoked stops with Fatal; 401 and 403 stop with Fatal; 429 waits Retry-After; 5xx backs off with growing waits; `retry:` replaces the base; a stable connection resets the backoff; stop returns promptly. Poller: first poll without If-None-Match; later with it; 304; 200 with ETag; invalid ruleset; 401 and 403 fatal; 429; 500; timeout. Sync: a stream put updates the store and the cache; a bootstrap apply does not write the cache; an invalid put keeps the previous ruleset; polling starts after two stream failures and stops on reconnect; the poll interval is respected; poll Retry-After; `disableStream` polls only and reports Stale then Ready; revoked is Fatal once; a 403 on a poll is Fatal; stop is idempotent and silences callbacks.

### B7. Events (Go: `internal/events`; `delivery.md` §7)
- **Evaluation record:** `{flagKey, variant, served, reason, contextKey ("" when the context key is not a string), context (only with sendContext), at}`.
- **Aggregator** (single owner): a count per (flag, variant or null when not served, UTC hour); an exposure only when served and (contextKey, flag, variant, hour) was not seen in an LRU of 10 000 keys; at most 10 000 pending exposures (more are dropped and counted; counts are never dropped). `take(1000)` removes up to 1000 exposures (oldest first) and up to 1000 count items (buckets in first-seen order; a bucket over 1 000 000 000 becomes several items). `restore(batch)` puts an unsent batch back in front and adds its counts to newer ones for the same bucket.
- **Body:** `{"exposures": [{"flag_key", "variant", "reason", "timestamp", "context"?}], "counts": [{"flag_key", "variant", "hour", "count"}]}`; both lists always present; `timestamp` like `2026-11-10T09:15:02.000Z`; `hour` like `2026-11-10T09:00:00Z`; `variant` is JSON null for a count of unserved evaluations; `context` only with sendContext.
- **Batcher:** `record(ev)` never blocks: a bounded queue of 10 000 (or direct aggregation on a single-threaded runtime); overflow, and any record after stop or shutdown, is dropped and counted. Flush every `flushInterval`, when either list reaches 1000 pending items, and on shutdown; a flush does nothing before `nextAttempt` and stops at the first failure.
- **Send one batch:** a body over 1 000 000 bytes is split before sending. Network error or 5xx: keep the batch, `nextAttempt = now + backoff.next()` (base 2 s, max 60 s). 2xx: reset the backoff. 429: keep, `nextAttempt = now + max(retryAfter, backoff.next())`. 413: split. 401 or 403: stop sending for good, drop everything, call `onFatal` once. Any other 4xx (422): drop the batch with a warning. Split: two halves by item count, exposures first; a single item that is too large is dropped; when the first half must be retried, the second half is kept unsent too.
- `shutdown(deadline 5 s)`: stop accepting, drain the queue, send until empty or the deadline, waiting out a retry delay when it fits; idempotent. `stop()`: stop without sending; idempotent.

**Tests to write.** Aggregator: one exposure per key, flag, variant and hour; anonymous contexts share one exposure; counts add up per hour; unserved evaluations have a null variant and no exposure; context only with sendContext; exposure fields and timestamp format; hour format; LRU eviction; maxPending drops exposures but not counts; take limits to 1000; take splits a count over 1e9; restore merges counts and keeps order. Batcher: bearer, user agent and JSON shape; flush on interval; flush at 1000; shutdown flushes; shutdown deadline; stop sends nothing; a full queue drops and counts without blocking; record after shutdown is dropped; 401 stops and calls onFatal once; 413 splits; a body over 1 MB is split before sending; a single oversized item is dropped; 422 drops; 429 retries after Retry-After; 500 retries with backoff; a split stops after a fatal status.

### B8. Provider (Go: `config.go`, `provider.go`, `lifecycle.go`)
**Config** (names per language in L1 to L6): `sdkKey`, `baseUrl`, `bootstrapFile`, `cacheDir`, `offline`, `initTimeout` (5 s), `disableStream`, `pollInterval` (30 s, raised to 15 s when smaller), `flushInterval` (30 s, clamped to 10 to 60 s), `sendContext`, `logger`, and the HTTP client hook in Node.js, Java and .NET. Validation, with messages that never contain the key: a key starting with `mf_client_` ("a client SDK key cannot be used by a server SDK; use a server key (mf_server_…)"); a key not starting with `mf_server_`; `offline` without a bootstrap file or cache directory; not offline and `baseUrl` is not an `http` or `https` URL with a host.

**Construction** (no network): validate (an invalid config is logged and remembered); create the store, cache, sync and, unless offline, the batcher; apply the bootstrap file when set (source `bootstrap`; a read error is a warning); when there is still no ruleset, load the cache (source `cache`; a corrupt file is a warning).

**Initialise:** invalid config: fail with the fatal code. Already shut down: fail. Start the batcher and, unless offline, the sync (once). A ruleset is present: return. Offline without one: fail (general error). Otherwise wait up to `initTimeout` for the first ruleset, a fatal status or shutdown: first ruleset returns; fatal fails with the fatal code; timeout fails with "no ruleset within <timeout>" (general error), remembers that init timed out, and keeps retrying in the background.

**Events:**
- `onChange(old, new)`: first ruleset: wake the waiting init; emit `PROVIDER_READY` only when init had timed out. Later rulesets: `PROVIDER_CONFIGURATION_CHANGED` with the sorted keys of flags added, removed or with a different `version` (the list may be empty).
- Status `Stale`: emit `PROVIDER_STALE` once, only with a ruleset and when not fatal. Status `Ready`: emit `PROVIDER_READY` when STALE had been emitted. Status `Fatal`, or the batcher's `onFatal`: once: log an error, stop the batcher without sending, stop the sync, emit `PROVIDER_ERROR` with the fatal error code. The last ruleset keeps being served.
- Nothing is emitted after shutdown.

**Shutdown** (idempotent): stop the sync, then `batcher.shutdown` with a 5 s deadline (log a warning when not everything was sent). The last ruleset keeps being served.

**Evaluation:** no ruleset: the default with `PROVIDER_FATAL` when the config is invalid or the key was refused, else `PROVIDER_NOT_READY`, reason `ERROR`. Otherwise copy the context attributes into a JSON-like map (unwrap SDK value types; a date value is left as the host type and matches nothing, as in Go), set `key` to the targeting key when there is one, call `evaluate` with the expected type (`boolean`, `string`, `number`, `json`), record the evaluation in the batcher (unless offline), and map the result: the value when a variation was served and there is no error, otherwise the caller's default; `reason` passed through (`PREREQUISITE_FAILED` included); `variant`; flag metadata `rule_index` (integer) and `prerequisite` (string); error code and message. An integer evaluation of a served number that is not integral is the default with `TYPE_MISMATCH` and reason `ERROR`. No I/O and no lock beyond reading the store.

**Tests to write.** Config: client key refused; non-server key refused; base URL validation; offline needs a file or a directory; defaults and clamps; messages do not contain the key. Evaluation (offline provider with a bootstrap file, through the real OpenFeature client): boolean, string, number, integer (integral and not), object; flag not found; type mismatch; targeting key becomes `key`; `PREREQUISITE_FAILED` reason; flag metadata; no ruleset is `PROVIDER_NOT_READY`; invalid config evaluates the default with the fatal code; an invalid bootstrap file leaves the provider not ready. Lifecycle (fake server): ready from the bootstrap file, then refreshed by the stream; bootstrap before cache; ready from the cache offline; offline without a ruleset fails; init waits for the first put; init timeout, then READY when the ruleset arrives; a 401 at init is fatal; a client key is fatal; `revoked` emits the fatal error and the last ruleset is still served; STALE then READY; CONFIGURATION_CHANGED lists the changed flags; events are sent on shutdown; shutdown twice; a record after shutdown is dropped.

### B9. End-to-end test (Go: `e2e/e2e_test.go`)
Skipped unless `MEGOOFLAGS_E2E_URL` and `MEGOOFLAGS_API_TOKEN` are both set. All management calls send `Authorization: Bearer <MEGOOFLAGS_API_TOKEN>` and JSON.
1. `POST /api/projects` `{"key": p, "name": p, "environments": [{"key": "e2e", "name": "E2E"}]}` gives 201, with `p` = `sdk<lang>-<8 random hex>`. Always `DELETE /api/projects/<p>` at the end.
2. `POST /api/projects/<p>/flags` `{"key": "e2e-flag", "name": "E2E", "type": "boolean"}` gives 201.
3. `POST /api/projects/<p>/flags/e2e-flag/environments/e2e/toggle` `{"enabled": true}` gives 200.
4. `POST /api/projects/<p>/environments/e2e/sdk-keys` `{"name": "<lang>-e2e", "kind": "server"}` gives 201; the key is in field `key`.
5. Create the provider with that key, the base URL, a temp cache directory and a 10 s flush interval; register it with the OpenFeature SDK and wait; subscribe to configuration-changed events; evaluate with targeting key `user-1`.
- **Check 1:** `e2e-flag` (default false) is true.
- **Check 2:** toggle to `{"enabled": false}`; the value becomes false within 5 s (poll every 50 ms), and a configuration-changed event arrives within 5 s.
- **Check 3:** evaluate 20 more times, shut the provider down, then `GET /api/projects/<p>/flags/e2e-flag/environments/e2e/evaluations?range=24h` reports `total >= 21` within 10 s.
- **Check 4:** find a closed local port (bind port 0, read it, close), create a new provider with base URL `http://127.0.0.1:<port>`, the same cache directory and a 1 s init timeout: initialisation succeeds and `e2e-flag` (default true) is false.

Local stack for running it: see Final verification.

### B10. CI jobs and docs per SDK
**`sdk-<lang>`** in `ci.yml`: `runs-on: ubuntu-24.04`, `strategy.fail-fast: false`, the version matrix of the design, `defaults.run.working-directory: sdks/<lang>`, then the steps listed in the SDK's L section (format check, lint, type check, unit tests with conformance, the p99 guard, package build). **`sdk-<lang>-e2e`**: `needs: images`; `actions/checkout@v4`; `uses: ./.github/actions/sdk-e2e-stack` with `project: megooflags-sdk-<lang>`; the runtime setup on the minimum version; the e2e command; then, as in `sdk-go-e2e`, `if: failure()` `docker compose -p megooflags-sdk-<lang> -f deploy/docker-compose.yml -f deploy/compose.ci.yml logs --no-color` and `if: always()` the same with `down -v`. Keep the action versions the workflow already uses for actions it already has (`actions/checkout@v4`, `actions/setup-node@v4`, `pnpm/action-setup@v4`, `astral-sh/setup-uv@v6`, `actions/upload-artifact@v4`); for new setup actions use the newest major (planning time: `actions/setup-java@v6`, `actions/setup-dotnet@v6`, `ruby/setup-ruby@v1`, `shivammathur/setup-php@v2`).

Example (Node.js); the other SDKs follow the same shape with the commands of their L section:
```yaml
  sdk-node:
    runs-on: ubuntu-24.04
    strategy:
      fail-fast: false
      matrix:
        node: ["22", "24", "26"]
    defaults:
      run:
        working-directory: sdks/node
    steps:
      - uses: actions/checkout@v4
      - uses: pnpm/action-setup@v4
        with:
          package_json_file: sdks/node/package.json
      - uses: actions/setup-node@v4
        with:
          node-version: ${{ matrix.node }}
          cache: pnpm
          cache-dependency-path: sdks/node/pnpm-lock.yaml
      - run: pnpm install --frozen-lockfile
      - run: pnpm format:check
      - run: pnpm lint
      - run: pnpm typecheck
      - run: pnpm test
      - name: p99 guard
        run: pnpm test:p99
      - run: pnpm build
      - run: pnpm pack

  sdk-node-e2e:
    runs-on: ubuntu-24.04
    needs: images
    steps:
      - uses: actions/checkout@v4
      - uses: ./.github/actions/sdk-e2e-stack
        with:
          project: megooflags-sdk-node
      - uses: pnpm/action-setup@v4
        with:
          package_json_file: sdks/node/package.json
      - uses: actions/setup-node@v4
        with:
          node-version: "22"
          cache: pnpm
          cache-dependency-path: sdks/node/pnpm-lock.yaml
      - name: E2E
        working-directory: sdks/node
        run: pnpm install --frozen-lockfile && pnpm e2e
      - if: failure()
        run: docker compose -p megooflags-sdk-node -f deploy/docker-compose.yml -f deploy/compose.ci.yml logs --no-color
      - if: always()
        run: docker compose -p megooflags-sdk-node -f deploy/docker-compose.yml -f deploy/compose.ci.yml down -v
```

**README** (`sdks/<lang>/README.md`), the same sections as `sdks/go/README.md`: intro and status (0.1.0, Alpha); Install; Quick start; Configuration (every option, default and note); Server keys only; How updates arrive; Offline and bootstrap; Events; OpenFeature events; Results; Context attributes (including: regular expressions behave like Python `re` with `re.ASCII`; patterns outside the portable subset never match); Versioning and license (tag `sdk-<lang>/vX.Y.Z`, `User-Agent`); Running the tests.

**`sdks/README.md`** status row: `| <Name> | [`sdks/<lang>`](<lang>/README.md) | 0.1.0 (Alpha) | OpenFeature provider | 100 % | `sdk-<lang>`, `sdk-<lang>-e2e` |`. **`CHANGELOG.md`**: one line under `[Unreleased]` / `### Added`, written for users like the Go SDK line: package name, 0.1.0 Alpha, what it does, the tag form.

## Languages

### L1. Node.js (`sdks/node`)
- **Tooling:** TypeScript, ESM only (`"type": "module"`, `exports` with `types` and `default` pointing to `dist/`), `engines.node` `>=22`, `packageManager` `pnpm@10.34.9` (the version `web/` uses), own `pnpm-lock.yaml`. Dev dependencies as in `web/`: `typescript ^5`, `eslint ^9` with `typescript-eslint ^8`, `prettier ^3`, `vitest ^5.1.0`, `@types/node ^22`. Scripts: `build` (`tsc -p tsconfig.build.json`), `lint` (`eslint .`), `format:check` (`prettier --check .`), `typecheck` (`tsc --noEmit`), `test` (`vitest run test/`), `test:p99` (`vitest run perf/`), `e2e` (`vitest run e2e/`). `files`: `dist`, `LICENSE`, `README.md`. Package name `@megooflags/node`, `publishConfig.access` `public`.
- **Check command:** `pnpm format:check && pnpm lint && pnpm typecheck && pnpm test && pnpm build`.
- **Files:** `package.json`, `pnpm-lock.yaml`, `tsconfig.json`, `tsconfig.build.json`, `eslint.config.js`, `.prettierrc.json`, `.prettierignore`, `vitest.config.ts`, `.gitignore` (`node_modules/`, `dist/`, `*.tgz`), `LICENSE`, `README.md`; `src/index.ts`, `src/version.ts`, `src/config.ts`, `src/provider.ts`; `src/internal/engine/{value,bucketing,semver,date,regex,model,parse,evaluate}.ts`; `src/internal/transport/{transport,backoff}.ts`; `src/internal/delivery/{store,cache,sse,streamer,poller,sync}.ts`; `src/internal/events/{aggregate,batcher}.ts`; tests in `test/` mirroring `src/` (`test/engine/*.test.ts`, `test/transport/*.test.ts`, `test/delivery/*.test.ts`, `test/events/*.test.ts`, `test/config.test.ts`, `test/provider.test.ts`, `test/lifecycle.test.ts`, `test/conformance.test.ts`, `test/version.test.ts`, `test/helpers/{fake-server,ruleset}.ts`), `perf/p99.test.ts`, `e2e/e2e.test.ts`.
- **Config:** `new MegooFlagsProvider({ sdkKey, baseUrl, bootstrapFile, cacheDir, offline, initTimeoutMs, disableStream, pollIntervalMs, flushIntervalMs, sendContext, logger, fetch })`. Durations are milliseconds. `logger` has `debug`, `info`, `warn`, `error` (default `console`).
- **OpenFeature** (`@openfeature/server-sdk`, peer dependency `^1.<latest minor>.0`, planning time 1.23.0 with `@openfeature/core` 1.12.0; also a dev dependency): `class MegooFlagsProvider implements Provider` with `metadata = { name: "MegooFlags" }`, `runsOn = "server"`, `events = new OpenFeatureEventEmitter()`, `initialize(context?)`, `onClose()`, `resolveBooleanEvaluation(flagKey, defaultValue, context, logger)`, `resolveStringEvaluation`, `resolveNumberEvaluation`, `resolveObjectEvaluation`, each returning `Promise<ResolutionDetails<T>>` (`value`, `variant`, `reason`, `errorCode`, `errorMessage`, `flagMetadata`). Events: `events.emit(ProviderEvents.ConfigurationChanged, { message, flagsChanged })`, `ProviderEvents.Stale`, `ProviderEvents.Ready`, `ProviderEvents.Error` with `{ message, errorCode: ErrorCode.PROVIDER_FATAL }`. `initialize` throws `ProviderFatalError` for a bad key. Context: `context.targetingKey` plus the other properties.
- **Runtime:** one event loop; global `fetch` (or the config's) with `AbortController`; the SSE reader consumes `response.body`; every timer is `unref()`ed; the open stream keeps the process alive until `OpenFeature.close()`. File I/O for the bootstrap file and the cache is synchronous (`node:fs`), at construction and on a ruleset change only.
- **CI:** matrix 22, 24, 26 (see B10 example). E2E on 22.

### L2. Python (`sdks/python`)
- **Tooling:** uv with its own `uv.lock`; `pyproject.toml`: name `megooflags`, version 0.1.0, `requires-python = ">=3.11"`, license Apache-2.0, dependency `openfeature-sdk` (lower bound = the latest released minor, planning time `>=0.10,<1`), build backend hatchling with `packages = ["src/megooflags"]` (as `api/`), dev group `ruff`, `mypy`, `pytest`; ruff as in `api/pyproject.toml` with `target-version = "py311"`; mypy `strict = true`, `files = ["src"]`; pytest `testpaths = ["tests"]`.
- **Check command:** `uv sync --frozen && uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest && uv build`. p99: `uv run pytest perf -s`. E2E: `uv run pytest e2e -v`.
- **Files:** `pyproject.toml`, `uv.lock`, `.gitignore` (`dist/`), `LICENSE`, `README.md`; `src/megooflags/__init__.py`, `py.typed`, `_version.py` (`__version__ = "0.1.0"`), `config.py`, `provider.py`; `src/megooflags/_engine/{__init__,model,bucketing,operators,parse,engine}.py`; `src/megooflags/_transport.py`; `src/megooflags/_delivery/{__init__,store,cache,sse,streamer,poller,sync}.py`; `src/megooflags/_events/{__init__,aggregate,batcher}.py`; `tests/conftest.py`, `tests/fake_server.py`, `tests/ruleset_gen.py`, `tests/test_version.py`, `tests/test_engine_{bucketing,operators,parse,engine}.py`, `tests/test_conformance.py`, `tests/test_transport.py`, `tests/test_delivery_{store,cache,sse,streamer,poller,sync}.py`, `tests/test_events_{aggregate,batcher}.py`, `tests/test_config.py`, `tests/test_provider.py`, `tests/test_lifecycle.py`, `tests/test_fork.py`; `perf/test_p99.py`; `e2e/test_e2e.py`.
- **Config:** `MegooFlagsProvider(*, sdk_key, base_url="", bootstrap_file=None, cache_dir=None, offline=False, init_timeout=5.0, disable_stream=False, poll_interval=30.0, flush_interval=30.0, send_context=False, logger=None)`; durations are seconds (`float`) or `timedelta`; it builds the frozen dataclass `MegooFlagsConfig` (exported). Default logger `logging.getLogger("megooflags")`.
- **OpenFeature** (planning time 0.10.0): `from openfeature.provider import AbstractProvider, Metadata`; `from openfeature.flag_evaluation import FlagResolutionDetails, Reason`; `from openfeature.exception import ErrorCode, ProviderFatalError, GeneralError`; `from openfeature.event import ProviderEventDetails`. Methods: `get_metadata()`, `get_provider_hooks()`, `initialize(evaluation_context)`, `shutdown()`, `resolve_boolean_details(flag_key, default_value, evaluation_context=None)`, `resolve_string_details`, `resolve_integer_details`, `resolve_float_details`, `resolve_object_details`. `FlagResolutionDetails(value, error_code, error_message, reason, variant, flag_metadata)`; `reason` may be any string. Events: `self.emit_provider_ready(ProviderEventDetails(...))`, `emit_provider_stale`, `emit_provider_configuration_changed(ProviderEventDetails(flags_changed=[...]))`, `emit_provider_error(ProviderEventDetails(error_code=ErrorCode.PROVIDER_FATAL, message=...))`. Context: `evaluation_context.targeting_key`, `.attributes`. Integer evaluation returns `int`, float evaluation `float`.
- **Runtime:** daemon threads; `http.client` with a 45 s socket timeout on the stream; `os.register_at_fork(after_in_child=...)` restarts the threads of live providers (weak references) in a forked child with empty event buffers.
- **Engine:** a copy of `api/src/megooflags_api/evaluation/` with three changes (step 11). No regex translator.
- **CI:** matrix `3.11`, `3.12`, `3.13`, `3.14`, `3.15` with `astral-sh/setup-uv@v6` (`python-version: ${{ matrix.python }}`, `enable-cache: true`, `cache-dependency-glob: sdks/python/uv.lock`); steps `uv sync --frozen`, `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy`, `uv run pytest`, `uv run pytest perf -s`, `uv build`. E2E on 3.11.

### L3. Java (`sdks/java`)
- **Tooling:** Maven; `io.github.mmhy2003:megooflags:0.1.0`; `maven.compiler.release` 11; compiler arguments `-Xlint:all -Werror` (if class files of the OpenFeature SDK raise `classfile` or `processing` lint warnings, use `-Xlint:all,-classfile,-processing` and say so in the commit); dependency `dev.openfeature:sdk` (latest 1.x, planning time 1.23.0; it brings `slf4j-api`); test dependency `org.junit.jupiter:junit-jupiter` (latest 5.x, planning time 5.14.4; JUnit 6 needs Java 17); surefire excludes the JUnit tags `p99` and `e2e` by default, profiles `p99` and `e2e` run only that tag; profile `format` (activation `<jdk>[17,)</jdk>`) binds `spotless:check` (Spotless with palantir-java-format) to `verify`; POM metadata `name`, `description`, `url`, Apache-2.0 `licenses`, `developers`, `scm`. Java 11 language level only: no records, switch expressions, text blocks or pattern matching.
- **Check command:** `mvn -B -ntp verify`. p99: `mvn -B -ntp test -Pp99`. E2E: `mvn -B -ntp test -Pe2e`. Format fix: `mvn spotless:apply`.
- **Files:** `pom.xml`, `.gitignore` (`target/`), `LICENSE`, `README.md`; under `src/main/java/io/github/mmhy2003/megooflags/`: `MegooFlagsProvider.java`, `MegooFlagsConfig.java`, `internal/BuildInfo.java` (`VERSION = "0.1.0"`), `internal/json/{Json,JsonException}.java`, `internal/engine/{Values,Bucketing,Semver,Dates,PortableRegex,RegexSyntaxException,Ruleset,RulesetParser,ParseException,Evaluator,Result}.java`, `internal/transport/{Transport,Backoff,FatalException,RetryAfterException,HttpStatusException}.java`, `internal/delivery/{Snapshot,Store,Cache,SseReader,Streamer,Poller,Sync}.java`, `internal/events/{Evaluation,Aggregator,Batcher}.java`; tests under `src/test/java/io/github/mmhy2003/megooflags/` mirroring these, plus `ConformanceTest`, `P99Test` (`@Tag("p99")`), `FakeServer` (`com.sun.net.httpserver.HttpServer`), `RulesetGen`, `e2e/EndToEndTest` (`@Tag("e2e")`).
- **Internal JSON** (about 300 lines): `Json.parse(String)` returns `LinkedHashMap<String,Object>`, `ArrayList<Object>`, `String`, `Long` (integer literal that fits) or `Double`, `Boolean`, `null`; strict RFC 8259 (refuse trailing data, trailing commas, leading zeros, bare `NaN`, unescaped control characters, bad escapes, a lone `-`), nesting limit 512, surrogate escapes combined, duplicate keys last wins. `Json.write(Object)` writes the same tree (escapes quote, backslash and control characters; non-ASCII as is; non-finite numbers refused). Tests: each of those points and a round trip.
- **Config:** `MegooFlagsConfig.builder().sdkKey(String).baseUrl(String).bootstrapFile(Path).cacheDir(Path).offline(boolean).initTimeout(Duration).disableStream(boolean).pollInterval(Duration).flushInterval(Duration).sendContext(boolean).logger(System.Logger).httpClient(HttpClient).build()`; `new MegooFlagsProvider(config)`; `MegooFlagsProvider.VERSION`. Default logger `System.getLogger("io.github.mmhy2003.megooflags")`.
- **OpenFeature:** `class MegooFlagsProvider extends dev.openfeature.sdk.EventProvider`: `getMetadata()` (name `MegooFlags`), `initialize(EvaluationContext)`, `shutdown()` (call `super.shutdown()` last), `getBooleanEvaluation(String, Boolean, EvaluationContext)`, `getStringEvaluation`, `getIntegerEvaluation`, `getDoubleEvaluation`, `getObjectEvaluation(String, Value, EvaluationContext)`, each returning `ProviderEvaluation<T>` built with `value`, `variant`, `reason` (a String), `errorCode`, `errorMessage`, `flagMetadata` (`ImmutableMetadata.builder().addInteger("rule_index", n).addString("prerequisite", k).build()`). Events: `emitProviderConfigurationChanged(ProviderEventDetails.builder().flagsChanged(list).message(m).build())`, `emitProviderStale`, `emitProviderReady`, `emitProviderError(ProviderEventDetails.builder().errorCode(ErrorCode.PROVIDER_FATAL).message(m).build())`. `initialize` throws `dev.openfeature.sdk.exceptions.FatalError` for a bad or refused key and `GeneralError` otherwise. Context: `ctx.asObjectMap()` (remove the `targetingKey` entry) and `ctx.getTargetingKey()`. Object values: convert the JSON tree with `Value.objectToValue` after turning each `Long` into `Integer` when it fits, else `Double`. `getIntegerEvaluation`: an integral number inside the `int` range, else `TYPE_MISMATCH`.
- **Runtime:** daemon threads; `java.net.http.HttpClient` (or the config's) with `HttpResponse.BodyHandlers.ofInputStream()`; a watchdog on a daemon scheduler closes a stream that is silent for 45 s; `Store` is an `AtomicReference`; the batcher queue is an `ArrayBlockingQueue` with `offer`.
- **CI:** matrix 11, 17, 21, 25 with `actions/setup-java` (`distribution: temurin`, `cache: maven`); steps `mvn -B -ntp verify`, `mvn -B -ntp test -Pp99`. E2E on 11 with `mvn -B -ntp test -Pe2e`.

### L4. .NET (`sdks/dotnet`)
- **Tooling:** `MegooFlags.sln`; `src/MegooFlags/MegooFlags.csproj` (`net8.0`, `Nullable` enable, `TreatWarningsAsErrors` true, `PackageId` `MegooFlags`, `Version` `0.1.0`, `PackageLicenseExpression` `Apache-2.0`, `PackageReadmeFile`, `InternalsVisibleTo` `MegooFlags.Tests`, package reference `OpenFeature` latest 2.x, planning time 2.14.1); `tests/MegooFlags.Tests/MegooFlags.Tests.csproj` with `<TargetFramework>$(TestTargetFramework)</TargetFramework>` defaulting to `net8.0`, `xunit` 2.9.x, `xunit.runner.visualstudio`, `Microsoft.NET.Test.Sdk`; `.editorconfig` for `dotnet format`. Test categories by trait: `[Trait("Category", "P99")]`, `[Trait("Category", "E2E")]`; the e2e test uses a small `E2EFactAttribute : FactAttribute` that sets `Skip` when the two variables are missing.
- **Check command:** `dotnet format --verify-no-changes && dotnet build -c Release && dotnet test -c Release --no-build --filter "Category!=P99&Category!=E2E" && dotnet pack src/MegooFlags -c Release --no-build -o out`. p99: `dotnet test -c Release --filter "Category=P99"`. E2E: `dotnet test -c Release --filter "Category=E2E"`.
- **Files:** `MegooFlags.sln`, `.editorconfig`, `.gitignore` (`bin/`, `obj/`, `out/`), `LICENSE`, `README.md`; `src/MegooFlags/MegooFlags.csproj`, `MegooFlagsProvider.cs`, `MegooFlagsOptions.cs`, `Internal/BuildInfo.cs`, `Internal/Json/JsonTree.cs`, `Internal/Engine/{Values,Bucketing,Semver,Dates,PortableRegex,Ruleset,RulesetParser,ParseException,Evaluator,Result}.cs`, `Internal/Transport/{Transport,Backoff,TransportExceptions}.cs`, `Internal/Delivery/{Snapshot,Store,Cache,SseReader,Streamer,Poller,Sync}.cs`, `Internal/Events/{Evaluation,Aggregator,Batcher}.cs`; `tests/MegooFlags.Tests/` mirroring these, plus `ConformanceTests.cs`, `P99Tests.cs`, `FakeServer.cs` (`HttpListener`), `RulesetGen.cs`, `E2E/EndToEndTests.cs`.
- **JSON:** `System.Text.Json`. `JsonTree.Parse(string)` turns a `JsonDocument` into `Dictionary<string, object?>`, `List<object?>`, `string`, `long` (when the raw text is an integer literal that fits) or `double`, `bool`, `null`; `JsonTree.Write(object?)` uses `Utf8JsonWriter`.
- **Config:** `new MegooFlagsProvider(new MegooFlagsOptions { SdkKey, BaseUrl, BootstrapFile, CacheDir, Offline, InitTimeout, DisableStream, PollInterval, FlushInterval, SendContext, Logger, HttpClient })` (`TimeSpan` durations, `ILogger?` default `NullLogger.Instance`, `HttpClient?`); `MegooFlagsProvider.Version`.
- **OpenFeature:** `class MegooFlagsProvider : OpenFeature.FeatureProvider`: `GetMetadata()` (`new Metadata("MegooFlags")`), `InitializeAsync(EvaluationContext, CancellationToken)`, `ShutdownAsync(CancellationToken)`, `ResolveBooleanValueAsync(string flagKey, bool defaultValue, EvaluationContext? context = null, CancellationToken cancellationToken = default)`, `ResolveStringValueAsync`, `ResolveIntegerValueAsync` (`int`), `ResolveDoubleValueAsync`, `ResolveStructureValueAsync` (`Value`), returning `ResolutionDetails<T>(flagKey, value, errorType, reason, variant, errorMessage, flagMetadata)`. On an error the provider returns the caller's default itself with the `ErrorType` (the .NET client does not replace the value). `InitializeAsync` throws `OpenFeature.Error.ProviderFatalException` (exactly that type) for a bad or refused key and `GeneralException` otherwise. Events: `new ProviderEventPayload { ProviderName = "MegooFlags", Type = ProviderEventTypes.ProviderConfigurationChanged, Message, FlagsChanged }`, `ProviderStale`, `ProviderReady`, `ProviderError` with `ErrorType = ErrorType.ProviderFatal`. They are queued on an internal unbounded channel and one pump task writes them to `EventChannel.Writer`, so delivery never blocks on a handler. Context: `context.TargetingKey` and `context.AsDictionary()` (skip the `targetingKey` entry), unwrapping `Value` (`IsBoolean`, `IsString`, `IsNumber` as double, `IsList`, structure as an object). Structure values: build `Value` and `Structure` from the JSON tree by hand (no date coercion). `ResolveIntegerValueAsync`: an integral number inside the `int` range, else `TypeMismatch`.
- **Runtime:** tasks with `CancellationToken`; `HttpClient.SendAsync(request, HttpCompletionOption.ResponseHeadersRead, ct)`; the 45 s dead timer is a linked `CancellationTokenSource` whose `CancelAfter` is reset before each read; `Store` uses `Volatile.Read/Write` or `Interlocked.Exchange`; the batcher queue is a bounded `Channel` with `TryWrite`.
- **CI:** matrix `8.0.x` (`TestTargetFramework=net8.0`) and `10.0.x` (`TestTargetFramework=net10.0`) with `actions/setup-dotnet`; steps `dotnet format --verify-no-changes`, `dotnet build -c Release -p:TestTargetFramework=<tfm>`, the unit `dotnet test` with the same property, the P99 `dotnet test`, `dotnet pack`. E2E on 8.

### L5. Ruby (`sdks/ruby`)
- **Tooling:** gem `megooflags`; `megooflags.gemspec` (`required_ruby_version >= 3.4`, license Apache-2.0, runtime dependency `openfeature-sdk ~> 0.8`, files `lib/**/*.rb`, `LICENSE`, `README.md`); `Gemfile` (`gemspec`, plus `minitest`, `rake`, `rubocop`), `Gemfile.lock` committed; `Rakefile` with tasks `test` (`test/**/*_test.rb`), `p99` (`perf/p99_test.rb`), `e2e` (`e2e/e2e_test.rb`); `.rubocop.yml` (`TargetRubyVersion: 3.4`, `NewCops: enable`).
- **Check command:** `bundle exec rubocop && bundle exec rake test && gem build megooflags.gemspec`. p99: `bundle exec rake p99`. E2E: `bundle exec rake e2e`.
- **Files:** `megooflags.gemspec`, `Gemfile`, `Gemfile.lock`, `Rakefile`, `.rubocop.yml`, `.gitignore` (`*.gem`, `pkg/`), `LICENSE`, `README.md`; `lib/megooflags.rb`, `lib/megooflags/version.rb` (`MegooFlags::VERSION = "0.1.0"`), `lib/megooflags/config.rb`, `lib/megooflags/provider.rb`, `lib/megooflags/internal/log.rb`, `lib/megooflags/internal/fork_hook.rb`, `lib/megooflags/internal/engine/{values,bucketing,semver,dates,regex,ruleset,parser,evaluator}.rb`, `lib/megooflags/internal/transport/{transport,backoff}.rb`, `lib/megooflags/internal/delivery/{store,cache,sse,streamer,poller,sync}.rb`, `lib/megooflags/internal/events/{aggregator,batcher}.rb`; `test/test_helper.rb`, `test/support/{fake_server,ruleset_gen}.rb` (the fake server is a small HTTP/1.1 server on `TCPServer`; WEBrick is not in the standard library), tests mirroring `lib/` as `test/**/*_test.rb`, `test/conformance_test.rb`, `perf/p99_test.rb`, `e2e/e2e_test.rb`.
- **Config:** `MegooFlags::Provider.new(sdk_key:, base_url: nil, bootstrap_file: nil, cache_dir: nil, offline: false, init_timeout: 5, disable_stream: false, poll_interval: 30, flush_interval: 30, send_context: false, logger: nil)` (seconds); it builds a frozen `MegooFlags::Config`. Logger: any object with `debug`, `info`, `warn`, `error`; the default tries `require "logger"` and `Logger.new($stderr, level: Logger::WARN)`, and on `LoadError` uses `MegooFlags::Internal::Log::Stderr` (writes with `Kernel#warn`).
- **OpenFeature** (0.8.1): the provider is duck-typed. `include OpenFeature::SDK::Provider::EventEmitter`; `def emits_lifecycle_events? = true`; `metadata` returns `OpenFeature::SDK::Provider::ProviderMetadata.new(name: "MegooFlags")`; `init(evaluation_context = nil)` emits `PROVIDER_READY` itself when it succeeds, and on failure emits `PROVIDER_ERROR` (with `error_code: OpenFeature::SDK::Provider::ErrorCode::PROVIDER_FATAL` for a bad or refused key) and re-raises; `shutdown`; `fetch_boolean_value(flag_key:, default_value:, evaluation_context: nil)`, `fetch_string_value`, `fetch_number_value`, `fetch_integer_value`, `fetch_float_value`, `fetch_object_value`, each returning `OpenFeature::SDK::Provider::ResolutionDetails.new(value:, reason:, variant:, error_code:, error_message:, flag_metadata:)`. Events: `emit_event(OpenFeature::SDK::ProviderEvent::PROVIDER_CONFIGURATION_CHANGED, flags_changed: [...], message: "...")`, `PROVIDER_STALE`, `PROVIDER_READY`, `PROVIDER_ERROR`. Context: `evaluation_context.fields` (string keys; skip `"targeting_key"`), `evaluation_context.targeting_key`; nil context is an empty one. `fetch_integer_value` returns an `Integer` (integral only), `fetch_float_value` a `Float` (`to_f`), `fetch_number_value` the number as is, `fetch_object_value` a `Hash` or `Array` (anything else is `TYPE_MISMATCH`).
- **Runtime:** threads; `Net::HTTP` with `read_body` and `read_timeout = 45` on the stream; `Digest::SHA256`; `JSON`; the batcher queue is a `SizedQueue` with a non-blocking push; a `Process._fork` hook (a module prepended to `Process.singleton_class`) restarts the threads of live providers in the child with empty event buffers.
- **CI:** matrix `3.4`, `4.0` with `ruby/setup-ruby@v1` (`bundler-cache: true`, `working-directory: sdks/ruby`); steps `bundle exec rubocop`, `bundle exec rake test`, `bundle exec rake p99`, `gem build megooflags.gemspec`. E2E on 3.4.

### L6. PHP (`sdks/php`)
- **Tooling:** Composer package `megooflags/megooflags` (no `version` field), `type` library, license Apache-2.0, `require`: `php >=8.3`, `ext-curl`, `ext-json`, `open-feature/sdk` (latest 2.x, planning time `^2.4`); `require-dev`: `phpunit/phpunit ^12.5`, `phpstan/phpstan ^2`, `friendsofphp/php-cs-fixer ^3`; PSR-4 `MegooFlags\` to `src/`, dev `MegooFlags\Tests\` to `tests/`; `"bin": ["bin/megooflags-sync"]`; `config.platform.php` `8.3.0`; `composer.lock` committed; scripts `test` (`phpunit --testsuite unit`), `p99` (`phpunit --testsuite p99`), `e2e` (`phpunit --testsuite e2e`), `stan` (`phpstan analyse`, level 8 on `src`), `cs` (`php-cs-fixer fix --dry-run --diff`). `.gitattributes` marks `tests/`, `phpunit.xml.dist`, `phpstan.neon.dist`, `.php-cs-fixer.dist.php` as `export-ignore`.
- **Check command:** `composer validate --strict && composer cs && composer stan && composer test`.
- **Files:** `composer.json`, `composer.lock`, `phpunit.xml.dist`, `phpstan.neon.dist`, `.php-cs-fixer.dist.php`, `.gitignore` (`vendor/`, `.phpunit.cache/`, `.php-cs-fixer.cache`), `.gitattributes`, `LICENSE`, `README.md`, `bin/megooflags-sync`; `src/Provider.php`, `src/Config.php`, `src/Version.php` (`MegooFlags\Version::VERSION = '0.1.0'`); `src/Internal/Engine/{Values,Bucketing,Semver,Dates,Regex,Compiler,ParseException,Evaluator,Result}.php`; `src/Internal/Transport/{Http,Response,Backoff}.php`; `src/Internal/Cache/{CacheFiles,State,Lock}.php`; `src/Internal/Events/{Recorder,Spool,Sender}.php`; `src/Internal/Upkeep.php`; `src/Internal/Daemon/{Daemon,SseParser,Lru}.php`; tests `tests/Engine/*Test.php`, `tests/ConformanceTest.php`, `tests/Perf/P99Test.php`, `tests/Support/{FakeServer.php,router.php,RulesetGen.php}`, `tests/Transport/*Test.php`, `tests/Cache/*Test.php`, `tests/Events/*Test.php`, `tests/UpkeepTest.php`, `tests/ProviderTest.php`, `tests/Daemon/*Test.php`, `tests/E2E/EndToEndTest.php`.
- **Config:** `new \MegooFlags\Provider(new \MegooFlags\Config(sdkKey: ..., baseUrl: ..., cacheDir: ..., bootstrapFile: null, offline: false, initTimeout: 5.0, disableStream: false, pollInterval: 30.0, flushInterval: 30.0, sendContext: false, logger: null))` (seconds; `cacheDir` is required; `logger` is a PSR-3 logger, which the OpenFeature SDK brings, default `NullLogger`).
- **OpenFeature** (2.4.0): `class Provider extends \OpenFeature\implementation\provider\AbstractProvider` with `protected static string $NAME = 'MegooFlags'`; `resolveBooleanValue(string $flagKey, bool $defaultValue, ?EvaluationContext $context = null): ResolutionDetails`, `resolveStringValue`, `resolveIntegerValue` (`int`), `resolveFloatValue` (`float`), `resolveObjectValue` (`array`); results built with `ResolutionDetailsBuilder` (`withValue`, `withVariant`, `withReason`, `withError(new ResolutionError(ErrorCode::FLAG_NOT_FOUND(), $message))`, `withMetadata`, `build`). On an error the provider returns the caller's default with `Reason::ERROR` itself (the client does not replace it). There is no init, shutdown, event or `PROVIDER_FATAL`: the provider loads lazily on the first evaluation, and an invalid config or a refused key without a cached ruleset is `PROVIDER_NOT_READY` with a message. The client checks types strictly: return `(float)` for float flags, `int` for integers (integral only), and for objects a PHP array (a served value that is not an array or object is `TYPE_MISMATCH`). Context: `$context->getTargetingKey()`, `$context->getAttributes()->toArray()`.
- **JSON in the engine:** `json_decode($text, false)`: objects are `stdClass`, arrays are PHP lists, so `{}` is not `[]`. `Evaluator::evaluate(array $ruleset, string $flagKey, mixed $context, mixed $default, ?string $expectedType)`: the context is valid when it is a `stdClass` (the provider passes `(object) $attributes`).
- **Compiled ruleset** (`Compiler::compile(mixed $tree): array`, B3 validation, raises `ParseException`): nested PHP arrays of scalars only, so the file below is constant data for OPcache:
```
['project' => string, 'environment' => string, 'warnings' => [string...],
 'flags' => [key => ['type' => string, 'enabled' => bool, 'version' => int,
     'variations' => [vkey => ['s' => scalar|null] | ['j' => '<json text of an array or object>']],
     'off' => ?string, 'prereqs' => [[flag, variation]...],
     'targets' => [[variation, [contextKey => true...]]...],
     'rules' => [['clauses' => [clause...], 'serve' => serve]...], 'fallthrough' => serve]],
 'segments' => [key => ['included' => [k => true...], 'excluded' => [k => true...], 'rules' => [[clause...]...]]]]
serve  = ['v' => variation] | ['by' => bucketBy, 'w' => [[variation, weight]...]]
clause = ['a' => attribute, 'op' => operator, 'n' => bool, 'p' => prepared values]
```
Prepared `matches` values are the translated PCRE pattern strings (B2). A `'j'` value is decoded when it is served (to arrays for the provider, to objects for the conformance runner).
- **Cache directory files** (`H` = first 16 hex of sha256 of the key):

| File | Content |
|---|---|
| `megooflags-H.json` | `{"etag", "ruleset"}` (B6 cache format) |
| `megooflags-H.php` | `<?php return ['etag' => string, 'source' => 'network' or 'bootstrap', 'ruleset' => <compiled ruleset>];` written with `var_export`, temp file and rename, then `opcache_invalidate($path, true)` when the function exists |
| `megooflags-H.state.json` | `{"checked_at": float, "etag": string, "failures": int, "next_attempt": float, "fatal": null or "401" or "403" or "revoked"}` (Unix seconds), written atomically |
| `megooflags-H.lock` | lock file for `flock(LOCK_EX or LOCK_NB)` |
| `megooflags-H.events.jsonl` | the spool: one line per request, `{"counts": [[flag, variant or null, hourEpochSeconds, n]...], "exposures": [{"flag_key", "variant", "reason", "timestamp", "context_key", "context"?}...]}`; appended with `FILE_APPEND or LOCK_EX`; when the file is 8 MiB or larger the line is dropped |

- **Request path (lazy load on the first evaluation):** read the state file; `require` the compiled file; when the state's `etag` differs from the compiled `etag`, call `opcache_invalidate` and `require` again. No compiled file: compile from `megooflags-H.json` when it exists and is valid (and write the compiled file); else from the bootstrap file (write the compiled file with `etag` empty and `source` bootstrap, not the JSON cache); else, unless offline or fatal or before `next_attempt`: cold start, wait for the lock with non-blocking tries every 50 ms up to `initTimeout`, look again for a compiled file, else `GET /sdk/v1/ruleset` with the remaining time as timeout, write both cache files and the state on 200, or record the failure in the state. A corrupt or half-written file is ignored. No ruleset: every evaluation returns the default with `PROVIDER_NOT_READY`.
- **Request end:** `Provider::flush()` (public; registered once with `register_shutdown_function` on the first evaluation): (1) unless offline or fatal, append this request's counts and exposures as one spool line; (2) upkeep is due when not offline, no fatal marker, `now - checked_at >= pollInterval`, `now >= next_attempt` and the non-blocking lock is acquired; then call `fastcgi_finish_request()` when it exists and run `Upkeep::run()`.
- **`Upkeep::run()`:** conditional `GET /sdk/v1/ruleset` (`If-None-Match` = the state's etag, 10 s timeout): 200 and valid: write both cache files; 304: nothing; 401 or 403: write the fatal marker and stop; 429, 5xx or network error: `failures += 1`, `next_attempt = now + max(retryAfter, backoff(failures))` with base 2 s and max 60 s. Then ship the spool (unless fatal): claim it by renaming to `megooflags-H.events.<pid>.<random>.sending` (also claim `.sending` files older than 60 s), sum the counts per (flag, variant, hour), keep one exposure per (context_key, flag, variant, hour), send batches of at most 1000 items per list with the B7 rules (body over 1 MB or 413 splits, 422 drops, 401 or 403 writes the fatal marker and drops, 429 or 5xx or a network error appends the unsent remainder back to the spool as one line and sets `failures` and `next_attempt`), delete the claimed file. Finally write the state with `checked_at = now` and release the lock.
- **Daemon** (`bin/megooflags-sync` runs `Daemon`): configuration from `MEGOOFLAGS_SDK_KEY` (environment only), and `MEGOOFLAGS_BASE_URL`, `MEGOOFLAGS_CACHE_DIR`, `MEGOOFLAGS_POLL_INTERVAL`, `MEGOOFLAGS_FLUSH_INTERVAL`, `MEGOOFLAGS_DISABLE_STREAM` or the flags `--base-url`, `--cache-dir`, `--poll-interval`, `--flush-interval`, `--disable-stream`, `--once`, `--help`. One loop on `curl_multi` with a 1 s select: the SSE stream (`CURLOPT_WRITEFUNCTION` feeding `SseParser`, B6 reader rules, 45 s dead timer measured per line, B6 streamer backoff and stop rules); the B6 polling fallback after two stream failures in a row, stopped on reconnect; on every valid `put` or 200 write both cache files and the state etag; every 5 s write the state with `checked_at = now` so requests skip upkeep; every `flushInterval` ship the spool as in `Upkeep` but with an `Lru` of 10 000 keys kept across shipments (one exposure per context key, flag, variant and UTC hour). SIGTERM and SIGINT (when `pcntl` is loaded) ship the spool once more and exit 0. `--once` does one conditional fetch and one shipment and exits 0. An invalid configuration, 401, 403 or `revoked` writes the fatal marker (when a cache directory is known) and exits with status 78. A fatal marker already present at start exits 78 at once.
- **Known difference** (README): without the daemon, exposures are deduplicated per shipped spool, not per hour.
- **CI:** matrix `8.3`, `8.4`, `8.5` with `shivammathur/setup-php@v2` (`extensions: curl, json`, `coverage: none`, `tools: composer`); steps `composer install --no-interaction --no-progress`, `composer validate --strict`, `composer cs` (on 8.3 only), `composer stan`, `composer test`, `composer p99`, `composer archive --format=zip --dir=out`. E2E on 8.3 with `composer e2e`.
- **Fake server for tests:** a child process `php -S 127.0.0.1:<port> tests/Support/router.php` whose router reads a scenario file from a temp directory (status, headers, body, streamed chunks with delays) and appends each request to a log file.

## Step reference material

### Step 1 cases (`spec/conformance/cases/operators_regex.json`)
A hit is `value true, variant on, reason TARGETING_MATCH, metadata {"rule_index": 0}`; a miss is `value false, variant off, reason DEFAULT, metadata {}`. `anchored` and `space` are existing flags.

| Flag | Pattern | Test name | `attr` | Result |
|---|---|---|---|---|
| `dot` | `^.$` | `dot-matches-carriage-return` | U+000D | hit |
| | | `dot-matches-line-separator` | U+2028 | hit |
| | | `dot-matches-astral-code-point` | U+1F600 | hit |
| | | `dot-excludes-newline` | U+000A | miss |
| `two-dots` | `^..$` | `astral-code-point-is-one-character` | U+1F600 | miss |
| `anchored` | `^ab$` | `dollar-not-before-carriage-return` | `ab` + U+000D | miss |
| `space` | `^\s$` | `space-excludes-no-break-space` | U+00A0 | miss |
| | | `space-excludes-next-line` | U+0085 | miss |
| `word` | `^\w+$` | `word-ascii` | `a_1` | hit |
| | | `word-excludes-non-ascii-letter` | `é` | miss |
| `word-boundary` | `a\bé` | `boundary-before-non-ascii-letter` | `aé` | hit |
| `not-word-boundary` | `a\Bé` | `no-non-boundary-before-non-ascii-letter` | `aé` | miss |
| `multiline` | `(?m)^b$` | `multiline-inner-line` | `a` LF `b` LF `c` | hit |
| `single-line` | `^b$` | `single-line-inner-line` | `a` LF `b` LF `c` | miss |
| `multiline-dollar` | `(?m)a$` | `multiline-dollar-before-newline` | `a` LF `b` | hit |
| | | `multiline-dollar-not-before-carriage-return` | `a` CR LF `b` | miss |
| `dotall` | `(?s)^a.b$` | `dotall-matches-newline` | `a` LF `b` | hit |
| `dot-inner` | `^a.b$` | `dot-does-not-match-newline` | `a` LF `b` | miss |
| `scoped-fold` | `^(?i:ab)c$` | `scoped-fold-inside-group` | `ABc` | hit |
| | | `scoped-fold-outside-group` | `ABC` | miss |
| `named-group` | `^(?P<x>ab)+c$` | `named-group` | `ababc` | hit |
| `text-start` | `(?m)\Ab` | `text-start-at-start` | `b` LF `a` | hit |
| | | `text-start-not-after-newline` | `a` LF `b` | miss |
| `bracket-in-class` | `^[a[]$` | `bracket-in-class-hit` | `[` | hit |
| | | `bracket-in-class-miss` | `b` | miss |
| `fold-class-range` | `(?i)^[j-l]$` | `fold-range-ascii` | `K` | hit |
| | | `fold-range-no-kelvin-sign` | U+212A | miss |

Sentence to append to the file's `description`: "`.` excludes only a newline and matches one code point; `\s`, `\w` and `\b` are ASCII-only; `(?m)`, `(?s)` and scoped `(?i:…)` flags, named groups, `\A` and a `[` inside a class behave as in Python."

### Step 2 files
`scripts/sdk_e2e_token.sh` (outline; prints only the token on stdout, messages on stderr):
```bash
#!/usr/bin/env bash
# Print an org admin service token for the SDK end-to-end tests: first-run setup (or sign-in when
# the stack is already set up), then POST /api/tokens, as scripts/smoke.sh does.
set -euo pipefail
base="${1:-${MEGOOFLAGS_E2E_URL:-http://localhost:8080}}"; base="${base%/}"
email="${MEGOOFLAGS_E2E_EMAIL:-admin@example.com}"
password="${MEGOOFLAGS_E2E_PASSWORD:-}"
setup_password="${password:-sdk-e2e-$(openssl rand -hex 16)}"
jar="$(mktemp)"; trap 'rm -f "$jar"' EXIT
json() { python3 -c 'import json, sys; print(json.dumps(dict(zip(sys.argv[1::2], sys.argv[2::2]))))' "$@"; }
post() { curl -sS -o /dev/null -w '%{http_code}' -b "$jar" -c "$jar" -H "Origin: $base" -H 'Content-Type: application/json' -d "$2" "$base$1"; }
code="$(post /api/setup "$(json email "$email" name 'SDK E2E' password "$setup_password")")"
case "$code" in
  200|201) ;;
  409)
    [ -n "$password" ] || { echo "the stack is already set up; set MEGOOFLAGS_E2E_PASSWORD" >&2; exit 1; }
    code="$(post /api/auth/sign-in "$(json email "$email" password "$password")")"
    case "$code" in 2*) ;; *) echo "POST /api/auth/sign-in: status $code" >&2; exit 1 ;; esac ;;
  *) echo "POST /api/setup: status $code" >&2; exit 1 ;;
esac
created="$(curl -fsS -b "$jar" -H "Origin: $base" -H 'Content-Type: application/json' \
  -d '{"name":"sdk-e2e","bindings":[{"role":"admin","scope":"org"}]}' "$base/api/tokens")" \
  || { echo "POST /api/tokens failed" >&2; exit 1; }
python3 -c 'import json, sys; print(json.load(sys.stdin)["secret"])' <<<"$created"
```
`.github/actions/sdk-e2e-stack/action.yml`:
```yaml
name: SDK e2e stack
description: Build the api and web images, start the compose stack and mint an API token for an SDK end-to-end test.
inputs:
  project:
    description: Compose project name, for example megooflags-sdk-node
    required: true
runs:
  using: composite
  steps:
    - id: v
      shell: bash
      run: echo "version=$(cat VERSION)" >> "$GITHUB_OUTPUT"
    - uses: docker/setup-buildx-action@v3
    - uses: docker/build-push-action@v6
      with:
        context: api
        load: true
        tags: megooflags-api:ci
        build-args: |
          VERSION=${{ steps.v.outputs.version }}
          REVISION=${{ github.sha }}
        cache-from: type=gha,scope=api
    - uses: docker/build-push-action@v6
      with:
        context: web
        load: true
        tags: megooflags-web:ci
        build-args: |
          VERSION=${{ steps.v.outputs.version }}
          REVISION=${{ github.sha }}
        cache-from: type=gha,scope=web
    - name: Stack
      shell: bash
      env:
        PROJECT: ${{ inputs.project }}
      run: |
        # Exported for the later steps too: compose needs the secret key for logs and down.
        {
          echo "MEGOOFLAGS_SITE_ADDRESS="
          echo "MEGOOFLAGS_SECRET_KEY=ci-sdk-e2e-secret-key-not-used-elsewhere-0123456789"
        } >> "$GITHUB_ENV"
        export MEGOOFLAGS_SITE_ADDRESS="" MEGOOFLAGS_SECRET_KEY=ci-sdk-e2e-secret-key-not-used-elsewhere-0123456789
        docker compose -p "$PROJECT" -f deploy/docker-compose.yml -f deploy/compose.ci.yml up -d --wait --wait-timeout 180 caddy
    - name: API token
      shell: bash
      run: |
        token="$(bash scripts/sdk_e2e_token.sh http://localhost:8080)"
        echo "::add-mask::$token"
        {
          echo "MEGOOFLAGS_E2E_URL=http://localhost:8080"
          echo "MEGOOFLAGS_API_TOKEN=$token"
        } >> "$GITHUB_ENV"
```

### Step 49 reference: release jobs
`scripts/check_sdk_version.py --sdk <node|python|java|dotnet|ruby|php> [--tag <tag>] [--root <dir>]` (stdlib only, same style as `scripts/check_version.py`): reads the version from the manifest, requires `X.Y.Z`, and with `--tag` requires the tag to be `sdk-<sdk>/vX.Y.Z` or `sdk-<sdk>/vX.Y.Z-rc.N` with the same `X.Y.Z`. Prints `sdk <sdk> version OK: X.Y.Z (tag …)` or the errors on stderr with exit status 1. Manifests: node `sdks/node/package.json` (`version`); python `sdks/python/pyproject.toml` (`project.version`); java `sdks/java/pom.xml` (the project's own `<version>`, read with `xml.etree` and the POM namespace); dotnet `sdks/dotnet/src/MegooFlags/MegooFlags.csproj` (`<Version>`); ruby `sdks/ruby/lib/megooflags/version.rb` (`VERSION = "…"`); php `sdks/php/src/Version.php` (`VERSION = '…'`).

`release.yml`: add to `on.push.tags`, for each of the six languages, `"sdk-<lang>/v[0-9]+.[0-9]+.[0-9]+"` and `"sdk-<lang>/v[0-9]+.[0-9]+.[0-9]+-rc.[0-9]+"`. The `meta` job already skips them (`startsWith(github.ref_name, 'v')`). Each job: `if: startsWith(github.ref_name, 'sdk-<lang>/v')`, `runs-on: ubuntu-24.04`, `permissions: contents: read` (plus `id-token: write` where the table says OIDC), then: checkout; `python3 scripts/check_sdk_version.py --sdk <lang> --tag "$GITHUB_REF_NAME"`; the runtime setup; the verify commands; the build; `actions/upload-artifact@v4` (`name: sdk-<lang>`, `if-no-files-found: error`); the publish step with `if: vars.SDK_PUBLISH_<LANG> == 'true' && !contains(github.ref_name, '-rc.')`.

| Job | Runtime | Verify | Build and artifact | Publish step |
|---|---|---|---|---|
| `sdk-node-release` (OIDC) | Node 24, pnpm | `pnpm install --frozen-lockfile`, `format:check`, `lint`, `typecheck`, `test` | `pnpm build`, `pnpm pack --pack-destination out`; `sdks/node/out/*.tgz` | `npm install -g npm@^11.5.1` then `npm publish out/*.tgz --access public` (trusted publishing, no token) |
| `sdk-python-release` (OIDC) | uv, Python 3.11 | `uv sync --frozen`, ruff check, ruff format check, mypy, pytest | `uv build`; `sdks/python/dist/*` | `pypa/gh-action-pypi-publish@release/v1` with `packages-dir: sdks/python/dist/` |
| `sdk-java-release` | Temurin 17, `server-id: central`, `server-username: CENTRAL_USERNAME`, `server-password: CENTRAL_PASSWORD` | `mvn -B -ntp verify` | `mvn -B -ntp -Prelease -Dgpg.skip=true package`; `sdks/java/target/*.jar` | `mvn -B -ntp -Prelease -DskipTests deploy` with env `CENTRAL_USERNAME`, `CENTRAL_PASSWORD`, `MAVEN_GPG_KEY` (secret `GPG_SIGNING_KEY`), `MAVEN_GPG_PASSPHRASE` (secret `GPG_PASSPHRASE`) |
| `sdk-dotnet-release` (OIDC) | .NET 8 | `dotnet format --verify-no-changes`, build, unit test | `dotnet pack src/MegooFlags -c Release -o out`; `sdks/dotnet/out/*.nupkg` | `NuGet/login@v1` with `user: ${{ vars.NUGET_USER }}`, then `dotnet nuget push out/*.nupkg --api-key <login output NUGET_API_KEY> --source https://api.nuget.org/v3/index.json` |
| `sdk-ruby-release` (OIDC) | Ruby 3.4 | `bundle exec rubocop`, `bundle exec rake test` | `gem build megooflags.gemspec`; `sdks/ruby/*.gem` | `rubygems/configure-rubygems-credentials` (newest tag, planning time `v2.1.0`), then `gem push megooflags-*.gem` |
| `sdk-php-release` | PHP 8.3, checkout with `fetch-depth: 0` | `composer install`, `composer validate --strict`, `composer stan`, `composer test` | `composer archive --format=zip --dir=out`; `sdks/php/out/*.zip` | `git subtree split --prefix=sdks/php -b sdk-php-split`; push that branch as `main` and as tag `vX.Y.Z` to `https://x-access-token:${SDK_PHP_SPLIT_TOKEN}@github.com/${{ vars.SDK_PHP_SPLIT_REPO || 'mmhy2003/megooflags-php' }}.git` |

The Java `release` profile (added to `sdks/java/pom.xml` in this step): `maven-source-plugin` (jar-no-fork), `maven-javadoc-plugin` (jar), `maven-gpg-plugin` (sign at `verify`, `gpg.signer` `bc`, skippable with `gpg.skip`), `org.sonatype.central:central-publishing-maven-plugin` (`extensions` true, `publishingServerId` `central`, `autoPublish` true).

### Step 50 reference: `sdks/RELEASING.md` and `AGENTS.md` §9
`sdks/RELEASING.md` sections: (1) how an SDK release works (bump the version in the manifest and the version constant in a PR into `develop`, release PR to `main`, a maintainer pushes `sdk-<lang>/vX.Y.Z`; the job verifies, builds and uploads the package, and publishes only when `SDK_PUBLISH_<LANG>` is `true`; `-rc` tags never publish; agents never push tags); (2) the one-time setup per registry, from the design's table: npm (`@megooflags` scope, a first manual publish, the trusted publisher bound to `release.yml`), PyPI (a pending trusted publisher for `megooflags`), Maven Central (namespace `io.github.mmhy2003`, secrets `CENTRAL_USERNAME`, `CENTRAL_PASSWORD`, `GPG_SIGNING_KEY`, `GPG_PASSPHRASE`), NuGet (trusted publishing policy, variable `NUGET_USER`), RubyGems (a pending trusted publisher for `megooflags`), Packagist (the read-only repository, its Packagist entry, secret `SDK_PHP_SPLIT_TOKEN`, optional variable `SDK_PHP_SPLIT_REPO`); (3) the repository variables `SDK_PUBLISH_NODE`, `SDK_PUBLISH_PYTHON`, `SDK_PUBLISH_JAVA`, `SDK_PUBLISH_DOTNET`, `SDK_PUBLISH_RUBY`, `SDK_PUBLISH_PHP`; (4) Go keeps its own flow (`sdks/go/vX.Y.Z`).

`AGENTS.md` §9: after the existing commands add an "SDK checks" block with, per SDK, the directory and the check, p99 and e2e commands of L1 to L6, the Go commands from `sdks/go/README.md`, and how to start the stack for the e2e tests (Final verification). §11: mention `sdks/RELEASING.md`.

## Steps

### 1. Spec: regex conformance cases where host regex engines differ
File: spec/conformance/cases/operators_regex.json only. No code changes.

Add the 15 new flags and 27 new tests of the plan table 'Step 1 cases'. Use the shape of the flags already in the file: boolean, variations on and off, off_variation off, one rule with one matches clause on attribute attr that serves on, fallthrough off. A hit expects value true, variant on, reason TARGETING_MATCH, error_code null, metadata rule_index 0. A miss expects value false, variant off, reason DEFAULT, error_code null, empty metadata. Every test uses context key u and default false. Append the sentence given under the table to the file description.

In JSON, double each backslash of a pattern; write control characters and U+2028, U+0085, U+00A0 and U+212A as JSON unicode escapes; write the accented letters and the emoji as literal UTF-8 characters, as the existing cases do.

These cases pin behaviour the reference engine and the Go SDK already have, so they pass at once. Run both:
- from api/: `uv run pytest tests/test_conformance.py -q` (expected: all pass, 27 more test_case ids than before);
- from sdks/go/: `go test -count=1 -run TestConformance ./...` (expected: ok).

If a case fails in the Python engine, the case is wrong: correct it against Python re with re.ASCII. If a case passes in Python but fails in the unchanged Go SDK, delete it from the JSON file and say so in the commit body: it stays a unit-test vector of plan section B2 (design rule: a case Go cannot pass stays a per-SDK unit test). Do not edit sdks/go.

Commit: `test(spec): regex conformance cases where host regex engines differ (MF-256)`

### 2. CI groundwork: shared e2e stack action, token script, sdks/README and AGENTS.md package managers
Files: .github/actions/sdk-e2e-stack/action.yml (new), scripts/sdk_e2e_token.sh (new, executable), scripts/tests/test_sdk_e2e_token.py (new), .github/workflows/ci.yml (job sdk-go-e2e only), sdks/README.md, AGENTS.md (section 1 only).

Test first: scripts/tests/test_sdk_e2e_token.py starts an http.server.ThreadingHTTPServer on 127.0.0.1 port 0 in a thread and runs `bash scripts/sdk_e2e_token.sh <url>` with subprocess. Tests: test_fresh_stack_prints_token (POST /api/setup answers 201 with a Set-Cookie; POST /api/tokens must carry that cookie, an Origin header equal to the base URL and a JSON body with name sdk-e2e and one binding role admin scope org; it answers 201 with secret mf_api_test; stdout is exactly that secret and a newline); test_existing_stack_signs_in (setup answers 409, MEGOOFLAGS_E2E_PASSWORD is set, POST /api/auth/sign-in receives the email and the password, then the token is printed); test_existing_stack_without_password_fails (409 and no password: exit status 1, empty stdout, stderr names MEGOOFLAGS_E2E_PASSWORD); test_token_error_fails (tokens answers 500: non-zero exit, empty stdout). Skip the module when bash or curl is missing. Run from the repository root: `uv run --frozen --project api pytest scripts/tests/test_sdk_e2e_token.py -q` -> fails: the script does not exist.

Implement the script and the composite action exactly as in plan section 'Step 2 files'. Rewrite job sdk-go-e2e to: checkout; the composite action with project megooflags-sdk-go; setup-go; the E2E step (it no longer sets MEGOOFLAGS_E2E_URL, the action exports it together with MEGOOFLAGS_API_TOKEN, which sdks/go/e2e already reads); then the existing logs (if failure) and down (if always) steps. Remove its job-level env block and the build and Stack steps the action now does.

Docs: sdks/README.md: the planned order becomes go, node, python, java, dotnet, ruby, php, js (browser), react, then mobile; add status rows for .NET (sdks/dotnet), Ruby (sdks/ruby) and PHP (sdks/php) marked planned. AGENTS.md section 1: the sdks/ row says 'In-repo SDKs, one directory per language (see sdks/README.md)'; replace the package manager bullets with: every project directory has exactly one package manager and at most one lockfile: uv for api and sdks/python, pnpm for web and sdks/node, Go modules for sdks/go, Maven for sdks/java, the dotnet CLI for sdks/dotnet, Bundler for sdks/ruby, Composer for sdks/php; never switch a directory to another package manager, never add a second lockfile to a directory, and commit lockfile changes with the manifest change that caused them.

Verify: the pytest command passes; from api/: `uv run ruff check ../scripts && uv run ruff format --check ../scripts`; `bash -n scripts/sdk_e2e_token.sh`; actionlint (Conventions) prints nothing.

Commit: `ci: shared SDK e2e stack action and token script (MF-256)`

### 3. Node.js: package scaffold and engine primitives
Read plan sections Conventions, B1 and L1 first. Everything is under sdks/node.

Create: package.json, pnpm-lock.yaml, tsconfig.json, tsconfig.build.json, eslint.config.js, .prettierrc.json, .prettierignore, vitest.config.ts, .gitignore, LICENSE (a copy of the root LICENSE), src/version.ts (VERSION = 0.1.0), src/index.ts (exports VERSION only for now), src/internal/engine/value.ts, bucketing.ts, semver.ts, date.ts, and the tests test/version.test.ts and test/engine/value.test.ts, bucketing.test.ts, semver.test.ts, date.test.ts.

Test first: write the four engine test files with every vector of B1, and test/version.test.ts (VERSION is 0.1.0 and equals the version in package.json). Run `corepack enable && pnpm install && pnpm test` -> fails: the modules under src/internal/engine do not exist.

Implement B1 with the exports isNumber, isScalar, jsonEqual, bucketText, bucket, pickVariation, parseSemver, compareSemver, parseRfc3339Millis, toMillis. Use node:crypto createHash for SHA-256 and the byte-wise modulo of B1. Every JSON number is a JS number; an integer is Number.isInteger.

Verify: the L1 check command passes; `git status` shows no dist or node_modules.

Commit: `feat(sdk-node): package scaffold and engine primitives (MF-256)`

### 4. Node.js: portable regex translator
Read B2 and L1. Files: sdks/node/src/internal/engine/regex.ts, sdks/node/test/engine/regex.test.ts.

Test first: regex.test.ts has (a) every row of the B2 vector table, (b) every pattern of the B2 refused list (a compile error), (c) the four emitter checks of B2 against the Node.js column, (d) the RegExp flags are exactly u. Run `pnpm test test/engine/regex.test.ts` -> fails: module not found.

Implement the three stages of B2: one recursive-descent parser over code points, sets as sorted merged code point ranges, and the emitter with the Node.js column. Export compilePortable(pattern), returning either an object with test(subject) or an error object with a reason. Build the RegExp with the single flag u; never use the flags i, m or s. A RegExp constructor error becomes a compile error.

Verify: the L1 check command passes.

Commit: `feat(sdk-node): portable regex translator (MF-256)`

### 5. Node.js: ruleset loader, evaluation, conformance at 100 % and the p99 guard
Read B3, B4 and L1. Files: src/internal/engine/model.ts, parse.ts, evaluate.ts; test/engine/parse.test.ts, evaluate.test.ts; test/conformance.test.ts; perf/p99.test.ts; test/helpers/ruleset.ts (the B4 generator).

Test first: parse.test.ts and evaluate.test.ts with the B3 test list; conformance.test.ts per B4 (reads ../../spec/conformance relative to sdks/node, one vitest case per <stem>::<name> and per bucketing::<input>); perf/p99.test.ts per B4. Run `pnpm test` -> fails: parse and evaluate do not exist.

Implement parseRuleset(textOrTree) (throws ParseError with the JSON path, collects regex warnings) and evaluate(ruleset, flagKey, context, defaultValue, expectedType) per B3. JS notes: an object is a non-null, non-array object; use Map for flags, segments and variations and Set for target and segment keys; read context attributes with own-property checks only, so a flag or an attribute named __proto__ or constructor behaves like any other key (add a test). JS cannot tell 1 from 1.0, so schema_version 1.0 is accepted.

Verify: `pnpm test` reports every conformance case as passed (100 %, the step 1 cases included); `pnpm test:p99` prints p50, p99 and max and passes with p99 under 1 ms; the L1 check command passes.

Commit: `feat(sdk-node): ruleset loader, evaluation and conformance runner (MF-256)`

### 6. Node.js: transport, store, cache and SSE reader
Read B5, B6 (Store, Cache, SSE reader) and L1. Files: src/internal/transport/transport.ts, backoff.ts; src/internal/delivery/store.ts, cache.ts, sse.ts; test/helpers/fake-server.ts; tests test/transport/transport.test.ts, backoff.test.ts, test/delivery/store.test.ts, cache.test.ts, sse.test.ts.

Test first: the B5 test list and the B6 test lists for Store, Cache and the SSE reader. fake-server.ts wraps node:http on 127.0.0.1 port 0: scripted responses (status, headers, body), a held-open SSE response the test writes lines to, a log of received requests, and close-all-connections. Run `pnpm test test/transport test/delivery` -> fails: modules not found.

Implement: buildHeaders, statusError (classes FatalError, RetryAfterError, HttpError), parseRetryAfter, Backoff, sleep(ms, signal) with an unref'd timer; Store; Cache (node:fs sync calls, temp file and rename, file mode 0o600, directory mode 0o700; the ruleset is embedded as received); readSse(async iterable of Uint8Array chunks, callbacks), splitting on the LF byte before decoding.

Verify: the L1 check command passes.

Commit: `feat(sdk-node): transport helpers, ruleset store, disk cache and SSE reader (MF-256)`

### 7. Node.js: streamer, poller and sync coordinator
Read B6 (Streamer, Poller, Sync) and L1. Files: src/internal/delivery/streamer.ts, poller.ts, sync.ts; tests test/delivery/streamer.test.ts, poller.test.ts, sync.test.ts, all against test/helpers/fake-server.ts.

Test first: the B6 test lists for Streamer, Poller and Sync, with timings shortened through the internal tuning object. Run `pnpm test test/delivery` -> fails: modules not found.

Implement with the global fetch (or the one from the config) and AbortController. The stream request has no total timeout; the dead timer aborts it after 45 s without a line; a poll has a 10 s timeout. Every timer is unref'd. Sync.start() returns at once and runs the loops as promises; Sync.stop() aborts them and resolves when they have ended. Every loop catches and logs, so there is never an unhandled rejection.

Verify: the L1 check command passes, and `pnpm test` exits by itself (no open handles).

Commit: `feat(sdk-node): SSE streamer, poller and sync coordinator (MF-256)`

### 8. Node.js: events aggregator and batcher
Read B7 and L1. Files: src/internal/events/aggregate.ts, batcher.ts; tests test/events/aggregate.test.ts, batcher.test.ts.

Test first: the B7 test lists. Run `pnpm test test/events` -> fails: modules not found.

Implement B7. Node.js has one thread, so record() adds to the aggregator directly: it is synchronous, does no I/O and never awaits; the pending count buckets are capped at 10 000 (more are dropped and counted), which stands in for the bounded queue. The flush timer is an unref'd setInterval; reaching 1000 pending items starts a flush without awaiting it; only one flush runs at a time. shutdown(deadlineMs) drains within the deadline and is idempotent; stop() sends nothing.

Verify: the L1 check command passes.

Commit: `feat(sdk-node): evaluation counts, exposure dedup and background batcher (MF-256)`

### 9. Node.js: provider, config and lifecycle
Read B8 and L1. Files: src/config.ts, src/provider.ts, src/index.ts (exports MegooFlagsProvider, the MegooFlagsConfig type and VERSION, nothing else); tests test/config.test.ts, test/provider.test.ts, test/lifecycle.test.ts; package.json gains the peer dependency @openfeature/server-sdk and the same package as a dev dependency (commit the lockfile change).

Test first: the B8 test lists through the real OpenFeature API (OpenFeature.setProviderAndWait with a domain per test, client.getBooleanDetails and the other typed calls, OpenFeature.addHandler for events, OpenFeature.close() in afterEach) and the fake server. Run `pnpm test` -> fails: src/provider does not exist.

Implement class MegooFlagsProvider as in L1. initialize throws ProviderFatalError for an invalid config or a refused key and a plain Error on timeout or offline without a ruleset; it does not emit Ready itself. A runtime fatal error emits ProviderEvents.Error with errorCode PROVIDER_FATAL. Errors are returned as errorCode and errorMessage with the caller's default as value. The JS SDK has no integer type, so the integer rule of B8 does not apply.

Verify: the L1 check command passes; after `pnpm build`, a one-line `node --input-type=module -e` that imports ./dist/index.js prints the exports MegooFlagsProvider and VERSION.

Commit: `feat(sdk-node): OpenFeature provider, config and lifecycle (MF-256)`

### 10. Node.js: end-to-end test, CI jobs and docs
Read B9, B10 and L1. Files: sdks/node/e2e/e2e.test.ts, sdks/node/README.md, .github/workflows/ci.yml (jobs sdk-node and sdk-node-e2e), sdks/README.md (the Node.js row), CHANGELOG.md.

Test first: e2e/e2e.test.ts implements B9 with fetch for the management API and is skipped (describe.skipIf) unless MEGOOFLAGS_E2E_URL and MEGOOFLAGS_API_TOKEN are set. Start a local stack (plan section Final verification), run `pnpm e2e`: it must pass; break the stream path on purpose once to see it fail, then restore. Without the two variables `pnpm e2e` reports the test as skipped.

CI: add the jobs sdk-node and sdk-node-e2e exactly as in the B10 example.

Docs: README.md with the B10 section list (install with `pnpm add @megooflags/node @openfeature/server-sdk`; an ESM quick start; the note that require() of this package needs Node.js 22.12 or later; the open stream keeps the process alive until OpenFeature.close()). sdks/README.md row and the CHANGELOG.md line as in B10 (package @megooflags/node, tags sdk-node/vX.Y.Z).

Verify: the L1 check command; `pnpm test:p99`; the e2e run above; actionlint prints nothing.

Commit: `feat(sdk-node): end-to-end test, CI jobs and docs (MF-256)`

### 11. Python: package scaffold, engine and conformance at 100 %
Read B1 to B4 and L2. Everything is under sdks/python.

Create: pyproject.toml, uv.lock (`uv lock`), .gitignore, LICENSE (a copy of the root LICENSE), src/megooflags/__init__.py (exports __version__ only for now), _version.py, py.typed, src/megooflags/_engine/ (__init__.py, model.py, bucketing.py, operators.py, parse.py, engine.py), tests/conftest.py, tests/test_version.py, tests/test_engine_bucketing.py, test_engine_operators.py, test_engine_parse.py, test_engine_engine.py, tests/test_conformance.py, tests/ruleset_gen.py, perf/test_p99.py.

Test first: copy api/tests/test_evaluation_bucketing.py, test_evaluation_operators.py, test_evaluation_parse.py and test_evaluation_engine.py into the four tests/test_engine_*.py files and change the imports from megooflags_api.evaluation to megooflags._engine; add the B1 date vectors and the B3 regex-warning test. tests/test_conformance.py: copy api/tests/test_conformance.py without the jsonschema tests and test_suite_is_complete, point SPEC at the repository's spec directory, and add a test that a file with schema_version 2 or 1.0 is refused. tests/test_version.py: __version__ is 0.1.0 and equals the version in pyproject.toml. perf/test_p99.py per B4. Run `uv run pytest` -> fails: megooflags._engine does not exist.

Implement: copy the modules of api/src/megooflags_api/evaluation/ into _engine/ and rename the imports. Then make exactly these changes: (1) to_millis parses with the explicit rules of B1 instead of datetime.fromisoformat, so Python 3.11 to 3.15 agree; (2) parse_ruleset also accepts JSON text and the Ruleset gains a warnings tuple naming each matches value that does not compile; (3) Variation gains is_int and int_value per B3. Keep re.compile(pattern, re.ASCII): Python needs no translator.

Verify: `uv run pytest` passes with every conformance id <stem>::<name>; `uv run pytest perf -s` prints p50, p99 and max and passes; `uv run ruff check . && uv run ruff format --check . && uv run mypy && uv build` pass.

Commit: `feat(sdk-python): package scaffold, engine and conformance runner (MF-256)`

### 12. Python: transport, store, cache and SSE reader
Read B5, B6 (Store, Cache, SSE reader) and L2. Files under sdks/python: src/megooflags/_transport.py; src/megooflags/_delivery/__init__.py, store.py, cache.py, sse.py; tests/fake_server.py; tests/test_transport.py, tests/test_delivery_store.py, test_delivery_cache.py, test_delivery_sse.py.

Test first: the B5 test list and the B6 test lists for Store, Cache and the SSE reader. tests/fake_server.py wraps http.server.ThreadingHTTPServer on 127.0.0.1 port 0: scripted responses, a held-open SSE response fed from a queue, a request log, close-all. Run `uv run pytest tests/test_transport.py tests/test_delivery_store.py tests/test_delivery_cache.py tests/test_delivery_sse.py` -> fails: modules not found.

Implement: build_headers, status_error with the exceptions FatalError, RetryAfterError and HttpError, parse_retry_after (email.utils.parsedate_to_datetime for the HTTP-date form), Backoff, and a small request helper on http.client (HTTPConnection or HTTPSConnection by URL scheme, with a timeout); Store (one attribute holding an immutable snapshot); Cache (hashlib, tempfile.mkstemp in the cache directory, os.replace, file mode 0o600, directory mode 0o700); read_sse(binary stream, callbacks) reading bytes line by line and decoding each line as UTF-8.

Verify: the L2 check command passes.

Commit: `feat(sdk-python): transport helpers, ruleset store, disk cache and SSE reader (MF-256)`

### 13. Python: streamer, poller and sync coordinator
Read B6 (Streamer, Poller, Sync) and L2. Files: src/megooflags/_delivery/streamer.py, poller.py, sync.py; tests/test_delivery_streamer.py, test_delivery_poller.py, test_delivery_sync.py.

Test first: the B6 test lists for Streamer, Poller and Sync, with timings shortened through the internal tuning object. Run them with `uv run pytest` -> fail: modules not found.

Implement with http.client and daemon threads. The stream connection has a 45 s socket timeout, so a read that times out is the dead-connection error. Sleeps wait on a threading.Event so that stop() ends them; stop() also closes the open connection to unblock a read, joins the threads with a short timeout and is idempotent. No callback runs after stop() or after a fatal status.

Verify: the L2 check command passes; a fixture asserts that threading.active_count() is back at its start value after each test.

Commit: `feat(sdk-python): SSE streamer, poller and sync coordinator (MF-256)`

### 14. Python: events aggregator and batcher
Read B7 and L2. Files: src/megooflags/_events/__init__.py, aggregate.py, batcher.py; tests/test_events_aggregate.py, test_events_batcher.py.

Test first: the B7 test lists. Run `uv run pytest tests/test_events_aggregate.py tests/test_events_batcher.py` -> fails: modules not found.

Implement B7: record() calls put_nowait on a queue.Queue(maxsize=10000) and counts a drop on queue.Full; one daemon thread owns the aggregator (LRU on collections.OrderedDict) and the sender; shutdown(deadline) and stop() as in B7; an injectable clock for the tests.

Verify: the L2 check command passes.

Commit: `feat(sdk-python): evaluation counts, exposure dedup and background batcher (MF-256)`

### 15. Python: provider, config, lifecycle and fork handling
Read B8 and L2. Files: src/megooflags/config.py, provider.py, __init__.py (exports MegooFlagsProvider, MegooFlagsConfig, __version__); tests/test_config.py, test_provider.py, test_lifecycle.py, test_fork.py; pyproject.toml gains the dependency openfeature-sdk (run `uv lock` and commit uv.lock).

Test first: the B8 test lists through the real OpenFeature API (openfeature.api: set_provider_and_wait with a domain per test, get_client, the typed get_*_details calls, add_handler for events, shutdown in teardown) and the fake server. tests/test_fork.py (skipped where os.fork is missing): after os.fork() the child has a running sync thread and an empty batcher, sends nothing the parent recorded, and the parent keeps working; the child reports through a pipe and ends with os._exit. Run `uv run pytest` -> fails: megooflags.provider does not exist.

Implement class MegooFlagsProvider(AbstractProvider) as in L2. initialize raises ProviderFatalError for an invalid config or a refused key and GeneralError on timeout or offline without a ruleset; it does not call emit_provider_ready itself. Errors are returned as FlagResolutionDetails with the default as value, reason ERROR, error_code and error_message. resolve_integer_details returns int and refuses a non-integral number with TYPE_MISMATCH; resolve_float_details returns float. os.register_at_fork(after_in_child=...) is registered once per process and walks a weakref.WeakSet of started providers; in the child each one drops its buffers and starts new threads.

Verify: the L2 check command passes.

Commit: `feat(sdk-python): OpenFeature provider, config, lifecycle and fork handling (MF-256)`

### 16. Python: end-to-end test, CI jobs and docs
Read B9, B10 and L2. Files: sdks/python/e2e/test_e2e.py, sdks/python/README.md, .github/workflows/ci.yml (jobs sdk-python and sdk-python-e2e), sdks/README.md (the Python row), CHANGELOG.md.

Test first: e2e/test_e2e.py implements B9 with urllib.request for the management API and is skipped (pytest.mark.skipif) unless MEGOOFLAGS_E2E_URL and MEGOOFLAGS_API_TOKEN are set. Start a local stack (Final verification) and run `uv run pytest e2e -v`: it must pass; break the stream path on purpose once to see it fail, then restore. Without the variables it is reported as skipped.

CI: job sdk-python with the matrix and steps of L2 (working directory sdks/python); job sdk-python-e2e in the B10 shape with project megooflags-sdk-python, setup-uv with Python 3.11, and the step `uv sync --frozen && uv run pytest e2e -v` in sdks/python.

Docs: README.md with the B10 section list (install with `pip install megooflags` or `uv add megooflags`; a quick start with openfeature.api; a paragraph on forking servers such as gunicorn and uWSGI: threads restart in each worker, event buffers start empty). sdks/README.md row and CHANGELOG.md line as in B10 (package megooflags on PyPI, tags sdk-python/vX.Y.Z).

Verify: the L2 check command; `uv run pytest perf -s`; the e2e run; actionlint prints nothing.

Commit: `feat(sdk-python): end-to-end test, CI jobs and docs (MF-256)`

### 17. Java: Maven scaffold, internal JSON and engine primitives
Read B1 and L3. Everything is under sdks/java.

Create: pom.xml (L3: coordinates, release 11, lint flags, JUnit 5, surefire tag exclusion, profiles p99, e2e and format, POM metadata; the OpenFeature dependency is added in step 23), .gitignore, LICENSE (a copy of the root LICENSE), and under the package io.github.mmhy2003.megooflags: internal/BuildInfo.java, internal/json/Json.java, JsonException.java, internal/engine/Values.java, Bucketing.java, Semver.java, Dates.java; tests BuildInfoTest, JsonTest, ValuesTest, BucketingTest, SemverTest, DatesTest.

Test first: JsonTest with the L3 internal JSON test points; the engine tests with every B1 vector; BuildInfoTest: BuildInfo.VERSION is 0.1.0 and equals the project version read from pom.xml. Run `mvn -B -ntp test` -> fails to compile: the classes do not exist.

Implement Json (L3) and B1. Numbers are Long or Double; two Longs compare exactly, anything else as doubles. Use MessageDigest SHA-256 and the byte-wise modulo. Java 11 language level only.

Verify: `mvn -B -ntp verify` passes (on JDK 17 or later it also runs the Spotless check; fix with `mvn spotless:apply`). If Docker is available, also run it once in maven:3.9-eclipse-temurin-11.

Commit: `feat(sdk-java): Maven scaffold, internal JSON reader and writer, engine primitives (MF-256)`

### 18. Java: portable regex translator
Read B2 and L3. Files: internal/engine/PortableRegex.java, RegexSyntaxException.java; test PortableRegexTest.

Test first: PortableRegexTest has (a) every row of the B2 vector table, (b) every pattern of the B2 refused list (RegexSyntaxException), (c) the four emitter checks of B2 against the Java column. Run `mvn -B -ntp test -Dtest=PortableRegexTest` -> fails to compile.

Implement the three stages of B2 with the Java column: PortableRegex.compile(String) throws RegexSyntaxException with a reason; test(String subject) is pattern.matcher(subject).find(). Call Pattern.compile(source) with no flags. Iterate the pattern by code point (String.codePointAt), not by char. A PatternSyntaxException from the emitted source is rethrown as RegexSyntaxException.

Verify: `mvn -B -ntp verify` passes.

Commit: `feat(sdk-java): portable regex translator (MF-256)`

### 19. Java: ruleset loader, evaluation, conformance at 100 % and the p99 guard
Read B3, B4 and L3. Files: internal/engine/Ruleset.java (the model classes), RulesetParser.java, ParseException.java, Evaluator.java, Result.java; tests RulesetParserTest, EvaluatorTest, ConformanceTest, RulesetGen (test helper), P99Test (tagged p99).

Test first: RulesetParserTest and EvaluatorTest with the B3 test list; ConformanceTest per B4 as JUnit dynamic tests named <stem>::<name> and bucketing::<input>, reading ../../spec/conformance relative to the module directory; P99Test per B4 with System.nanoTime. Run `mvn -B -ntp test` -> fails to compile.

Implement RulesetParser.parse(String json) and parse(Object tree) (ParseException with the JSON path; warnings on the Ruleset) and Evaluator.evaluate(ruleset, flagKey, context, defaultValue, expectedType) per B3. The context is valid when it is a Map; an integer is a Long (a Double such as 1.0 is not an integer for schema_version, version and weight); immutable model objects; HashMap and HashSet lookups.

Verify: `mvn -B -ntp verify` runs the conformance cases at 100 % (the step 1 cases included); `mvn -B -ntp test -Pp99` prints p50, p99 and max and passes.

Commit: `feat(sdk-java): ruleset loader, evaluation and conformance runner (MF-256)`

### 20. Java: transport, store, cache and SSE reader
Read B5, B6 (Store, Cache, SSE reader) and L3. Files: internal/transport/Transport.java, Backoff.java, FatalException.java, RetryAfterException.java, HttpStatusException.java; internal/delivery/Snapshot.java, Store.java, Cache.java, SseReader.java; tests TransportTest, BackoffTest, StoreTest, CacheTest, SseReaderTest, and the helper FakeServer (com.sun.net.httpserver.HttpServer on 127.0.0.1 port 0: scripted responses, a held-open SSE exchange the test writes to, a request log, stop).

Test first: the B5 test list and the B6 test lists for Store, Cache and the SSE reader. Run `mvn -B -ntp test` -> fails to compile.

Implement: Transport.request(method, url, key, userAgent, body) building a java.net.http.HttpRequest with the B5 headers, statusError, parseRetryAfter (DateTimeFormatter.RFC_1123_DATE_TIME for the HTTP-date form), Backoff with an injectable DoubleSupplier; Store on AtomicReference; Cache (Files.createTempFile in the cache directory, POSIX permissions where the file system supports them, Files.move with ATOMIC_MOVE and REPLACE_EXISTING); SseReader.read(InputStream, callbacks) splitting on the LF byte and decoding each line as UTF-8.

Verify: `mvn -B -ntp verify` passes.

Commit: `feat(sdk-java): transport helpers, ruleset store, disk cache and SSE reader (MF-256)`

### 21. Java: streamer, poller and sync coordinator
Read B6 (Streamer, Poller, Sync) and L3. Files: internal/delivery/Streamer.java, Poller.java, Sync.java; tests StreamerTest, PollerTest, SyncTest (against FakeServer).

Test first: the B6 test lists for Streamer, Poller and Sync, with timings shortened through the internal tuning object. Run `mvn -B -ntp test` -> fails to compile.

Implement with java.net.http.HttpClient (the one from the config or a default without a connect-independent timeout) and HttpResponse.BodyHandlers.ofInputStream(). A watchdog on a daemon ScheduledExecutorService closes the stream's InputStream when no line arrived for 45 s; the reader then fails with the dead-connection error. Polls use HttpRequest.timeout of 10 s. All threads are daemon threads named megooflags-*. Sync.stop() closes the stream, interrupts the workers, joins them with a short timeout and is idempotent.

Verify: `mvn -B -ntp verify` passes; run StreamerTest once on JDK 11 if available (closing the stream from another thread must unblock the read there too).

Commit: `feat(sdk-java): SSE streamer, poller and sync coordinator (MF-256)`

### 22. Java: events aggregator and batcher
Read B7 and L3. Files: internal/events/Evaluation.java, Aggregator.java, Batcher.java; tests AggregatorTest, BatcherTest.

Test first: the B7 test lists. Run `mvn -B -ntp test` -> fails to compile.

Implement B7: record() is offer on an ArrayBlockingQueue of 10 000 and counts a drop when it returns false; one daemon thread owns the aggregator (LRU on an access-ordered LinkedHashMap) and the sender; the body is written with the internal Json; shutdown(Duration) and stop() as in B7; an injectable clock.

Verify: `mvn -B -ntp verify` passes.

Commit: `feat(sdk-java): evaluation counts, exposure dedup and background batcher (MF-256)`

### 23. Java: provider, config and lifecycle
Read B8 and L3. Files: pom.xml (add the dependency dev.openfeature:sdk), MegooFlagsConfig.java, MegooFlagsProvider.java (with the public constant VERSION = BuildInfo.VERSION); tests MegooFlagsConfigTest, MegooFlagsProviderTest, LifecycleTest.

Test first: the B8 test lists through the real OpenFeature API (OpenFeatureAPI.getInstance(): setProviderAndWait with a domain per test, getClient, the typed get*Details calls, onProviderConfigurationChanged, onProviderStale, onProviderReady and onProviderError handlers, shutdown after each test) and FakeServer. Run `mvn -B -ntp test` -> fails to compile.

Implement as in L3: MegooFlagsProvider extends EventProvider. initialize throws FatalError for an invalid config or a refused key and GeneralError on timeout or offline without a ruleset; it does not emit READY itself. Results are ProviderEvaluation objects; errors carry errorCode and errorMessage with the caller's default as value. getIntegerEvaluation accepts only an integral number inside the int range; getObjectEvaluation converts the JSON tree to Value. shutdown() runs the B8 shutdown and then super.shutdown(). If -Xlint:all -Werror now fails on class files of the OpenFeature SDK, apply the allowance in L3.

Verify: `mvn -B -ntp verify` passes.

Commit: `feat(sdk-java): OpenFeature provider, config and lifecycle (MF-256)`

### 24. Java: end-to-end test, CI jobs and docs
Read B9, B10 and L3. Files: src/test/java/io/github/mmhy2003/megooflags/e2e/EndToEndTest.java (tagged e2e), sdks/java/README.md, .github/workflows/ci.yml (jobs sdk-java and sdk-java-e2e), sdks/README.md (the Java row), CHANGELOG.md.

Test first: EndToEndTest implements B9 with java.net.http.HttpClient and the internal Json for the management API, and is disabled unless MEGOOFLAGS_E2E_URL and MEGOOFLAGS_API_TOKEN are set (JUnit assumptions). Start a local stack (Final verification) and run `mvn -B -ntp test -Pe2e`: it must pass; break the stream path on purpose once to see it fail, then restore. Without the variables the test is reported as skipped.

CI: job sdk-java with the matrix and steps of L3 (working directory sdks/java); job sdk-java-e2e in the B10 shape with project megooflags-sdk-java, Temurin 11 and `mvn -B -ntp test -Pe2e` in sdks/java.

Docs: README.md with the B10 section list (Maven and Gradle coordinates io.github.mmhy2003:megooflags:0.1.0, Java 11 or later, a quick start with OpenFeatureAPI, logging through System.Logger). sdks/README.md row and CHANGELOG.md line as in B10 (tags sdk-java/vX.Y.Z).

Verify: `mvn -B -ntp verify`; `mvn -B -ntp test -Pp99`; the e2e run; actionlint prints nothing.

Commit: `feat(sdk-java): end-to-end test, CI jobs and docs (MF-256)`

### 25. .NET: solution scaffold, JSON tree and engine primitives
Read B1 and L4. Everything is under sdks/dotnet.

Create: MegooFlags.sln, .editorconfig, .gitignore, LICENSE (a copy of the root LICENSE), src/MegooFlags/MegooFlags.csproj (L4; the OpenFeature package reference is added in step 31), src/MegooFlags/Internal/BuildInfo.cs, Internal/Json/JsonTree.cs, Internal/Engine/Values.cs, Bucketing.cs, Semver.cs, Dates.cs; tests/MegooFlags.Tests/MegooFlags.Tests.csproj and the tests BuildInfoTests, JsonTreeTests, ValuesTests, BucketingTests, SemverTests, DatesTests.

Test first: the engine tests with every B1 vector; JsonTreeTests (integer literals become long, fractions and exponents double, an integer beyond long becomes double, objects, arrays, null, a round trip through Write); BuildInfoTests: BuildInfo.Version is 0.1.0 and equals the Version element of the csproj. Run `dotnet test` -> fails to build: the types do not exist.

Implement JsonTree (L4) and B1. Two longs compare exactly, anything else as doubles. Use SHA256.HashData and the byte-wise modulo. All internal types are internal; the test project sees them through InternalsVisibleTo.

Verify: the L4 check command passes.

Commit: `feat(sdk-dotnet): solution scaffold, JSON tree and engine primitives (MF-256)`

### 26. .NET: portable regex translator
Read B2 and L4. Files: src/MegooFlags/Internal/Engine/PortableRegex.cs; tests/MegooFlags.Tests/Engine/PortableRegexTests.cs.

Test first: PortableRegexTests has (a) every row of the B2 vector table, (b) every pattern of the B2 refused list (TryCompile returns false with a reason), (c) the four emitter checks of B2 against the .NET column, (d) surrogate handling: the astral rows of the table, a set that mixes BMP and astral ranges, and a lone surrogate subject against the dot pattern. Run `dotnet test --filter PortableRegexTests` -> fails to build.

Implement the three stages of B2 with the .NET column and the surrogate pair expansion described there: PortableRegex.TryCompile(string pattern, out PortableRegex regex, out string error); Test(string subject) is Regex.IsMatch. Create the Regex with RegexOptions.CultureInvariant only (never IgnoreCase, Multiline or Singleline). Iterate the pattern by code point (Rune). An ArgumentException from the Regex constructor becomes a compile error.

Verify: the L4 check command passes.

Commit: `feat(sdk-dotnet): portable regex translator (MF-256)`

### 27. .NET: ruleset loader, evaluation, conformance at 100 % and the p99 guard
Read B3, B4 and L4. Files: src/MegooFlags/Internal/Engine/Ruleset.cs (the model), RulesetParser.cs, ParseException.cs, Evaluator.cs, Result.cs; tests RulesetParserTests, EvaluatorTests, ConformanceTests, RulesetGen (helper), P99Tests (trait Category P99).

Test first: RulesetParserTests and EvaluatorTests with the B3 test list; ConformanceTests per B4 as a theory whose member data yields one row per <stem>::<name> and per bucketing::<input> (the display name is that id), locating spec/conformance by walking up from AppContext.BaseDirectory to the directory that contains spec/; P99Tests per B4 with Stopwatch.GetTimestamp. Run `dotnet test` -> fails to build.

Implement RulesetParser.Parse(string json) and Parse(object tree) and Evaluator.Evaluate(ruleset, flagKey, context, defaultValue, expectedType) per B3. The context is valid when it is a Dictionary<string, object?>; an integer is a long; immutable model; Dictionary and HashSet lookups with ordinal comparison.

Verify: the L4 check command runs the conformance cases at 100 % (the step 1 cases included); the L4 p99 command prints p50, p99 and max and passes.

Commit: `feat(sdk-dotnet): ruleset loader, evaluation and conformance runner (MF-256)`

### 28. .NET: transport, store, cache and SSE reader
Read B5, B6 (Store, Cache, SSE reader) and L4. Files: src/MegooFlags/Internal/Transport/Transport.cs, Backoff.cs, TransportExceptions.cs; Internal/Delivery/Snapshot.cs, Store.cs, Cache.cs, SseReader.cs; tests TransportTests, BackoffTests, StoreTests, CacheTests, SseReaderTests and the helper FakeServer (HttpListener on a free 127.0.0.1 port: scripted responses, a held-open SSE response the test writes to, a request log, stop).

Test first: the B5 test list and the B6 test lists for Store, Cache and the SSE reader (the file mode test runs on Unix only). Run `dotnet test` -> fails to build.

Implement: Transport.CreateRequest (HttpRequestMessage with the B5 headers), StatusError, ParseRetryAfter, Backoff with an injectable random source; Store (Volatile read, Interlocked.Exchange); Cache (temp file in the cache directory, File.SetUnixFileMode 0600 on Unix, File.Move with overwrite); SseReader.ReadAsync(Stream, callbacks, CancellationToken) splitting on the LF byte and decoding each line as UTF-8.

Verify: the L4 check command passes.

Commit: `feat(sdk-dotnet): transport helpers, ruleset store, disk cache and SSE reader (MF-256)`

### 29. .NET: streamer, poller and sync coordinator
Read B6 (Streamer, Poller, Sync) and L4. Files: src/MegooFlags/Internal/Delivery/Streamer.cs, Poller.cs, Sync.cs; tests StreamerTests, PollerTests, SyncTests (against FakeServer).

Test first: the B6 test lists for Streamer, Poller and Sync, with timings shortened through the internal tuning object. Run `dotnet test` -> fails to build.

Implement with HttpClient (the one from the options, or one created with Timeout.InfiniteTimeSpan) and SendAsync with HttpCompletionOption.ResponseHeadersRead. The dead timer is a CancellationTokenSource linked to the stop token whose CancelAfter(45 s) is reset before each read; a cancellation that is not the stop token is the dead-connection error. A poll uses a linked token with CancelAfter(10 s). Sync.Start() starts tasks; Sync.StopAsync() cancels them, awaits them and is idempotent. No task is left unobserved; no callback after stop or after a fatal status.

Verify: the L4 check command passes.

Commit: `feat(sdk-dotnet): SSE streamer, poller and sync coordinator (MF-256)`

### 30. .NET: events aggregator and batcher
Read B7 and L4. Files: src/MegooFlags/Internal/Events/Evaluation.cs, Aggregator.cs, Batcher.cs; tests AggregatorTests, BatcherTests.

Test first: the B7 test lists. Run `dotnet test` -> fails to build.

Implement B7: Record() is TryWrite on a bounded Channel of 10 000 and counts a drop when it returns false; one task owns the aggregator (LRU on a Dictionary plus a LinkedList) and the sender; the body is written with Utf8JsonWriter; ShutdownAsync(TimeSpan) and Stop() as in B7; an injectable TimeProvider.

Verify: the L4 check command passes.

Commit: `feat(sdk-dotnet): evaluation counts, exposure dedup and background batcher (MF-256)`

### 31. .NET: provider, options and lifecycle
Read B8 and L4. Files: src/MegooFlags/MegooFlags.csproj (add the OpenFeature package reference), MegooFlagsOptions.cs, MegooFlagsProvider.cs (with the public constant Version = BuildInfo.Version); tests MegooFlagsOptionsTests, MegooFlagsProviderTests, LifecycleTests.

Test first: the B8 test lists through the real OpenFeature API (Api.Instance.SetProviderAsync with a domain per test, GetClient, the typed Get*DetailsAsync calls, AddHandler for the four event types, ShutdownAsync after each test) and FakeServer. The .NET SDK does not rethrow an initialisation failure, so assert on the provider status and events, not on an exception. Add a test that a slow event handler does not delay applying the next ruleset. Run `dotnet test` -> fails to build.

Implement as in L4: MegooFlagsProvider : FeatureProvider. InitializeAsync throws ProviderFatalException for an invalid config or a refused key and GeneralException on timeout or offline without a ruleset; it does not emit ProviderReady itself. On every error the returned ResolutionDetails carries the caller's default. Events go through the internal channel and the single pump task into EventChannel. ResolveIntegerValueAsync accepts only an integral number inside the int range; ResolveStructureValueAsync converts the JSON tree to Value by hand.

Verify: the L4 check command passes.

Commit: `feat(sdk-dotnet): OpenFeature provider, options and lifecycle (MF-256)`

### 32. .NET: end-to-end test, CI jobs and docs
Read B9, B10 and L4. Files: tests/MegooFlags.Tests/E2E/EndToEndTests.cs and E2EFactAttribute.cs, sdks/dotnet/README.md, .github/workflows/ci.yml (jobs sdk-dotnet and sdk-dotnet-e2e), sdks/README.md (the .NET row), CHANGELOG.md.

Test first: EndToEndTests implements B9 with HttpClient and System.Text.Json for the management API; it has the trait Category E2E and is skipped by E2EFactAttribute unless MEGOOFLAGS_E2E_URL and MEGOOFLAGS_API_TOKEN are set. Start a local stack (Final verification) and run the L4 e2e command: it must pass; break the stream path on purpose once to see it fail, then restore. Without the variables the test is reported as skipped.

CI: job sdk-dotnet with the matrix and steps of L4 (working directory sdks/dotnet); job sdk-dotnet-e2e in the B10 shape with project megooflags-sdk-dotnet, .NET 8 and the L4 e2e command in sdks/dotnet.

Docs: README.md with the B10 section list (install with `dotnet add package MegooFlags`, net8.0 or later, a quick start with Api.Instance.SetProviderAsync, logging through ILogger, the error value is always the caller's default). sdks/README.md row and CHANGELOG.md line as in B10 (tags sdk-dotnet/vX.Y.Z).

Verify: the L4 check command; the L4 p99 command; the e2e run; actionlint prints nothing.

Commit: `feat(sdk-dotnet): end-to-end test, CI jobs and docs (MF-256)`

### 33. Ruby: gem scaffold and engine primitives
Read B1 and L5. Everything is under sdks/ruby.

Create: megooflags.gemspec (the openfeature-sdk dependency is added in step 39), Gemfile, Gemfile.lock (`bundle install`), Rakefile, .rubocop.yml, .gitignore, LICENSE (a copy of the root LICENSE), lib/megooflags.rb, lib/megooflags/version.rb, lib/megooflags/internal/engine/values.rb, bucketing.rb, semver.rb, dates.rb; test/test_helper.rb, test/version_test.rb, test/engine/values_test.rb, bucketing_test.rb, semver_test.rb, dates_test.rb.

Test first: the engine tests with every B1 vector; version_test.rb: MegooFlags::VERSION is 0.1.0 and the gemspec loads with that version. Run `bundle exec rake test` -> fails: the files under lib/megooflags/internal/engine cannot be loaded.

Implement B1 as module functions under MegooFlags::Internal::Engine. Integer and Float: two Integers compare exactly, anything else as floats; true and false are not numbers. Use Digest::SHA256 and the byte-wise modulo. Every file starts with the frozen_string_literal comment.

Verify: the L5 check command passes on your Ruby (3.4 or later).

Commit: `feat(sdk-ruby): gem scaffold and engine primitives (MF-256)`

### 34. Ruby: portable regex translator
Read B2 and L5. Files: lib/megooflags/internal/engine/regex.rb, test/engine/regex_test.rb.

Test first: regex_test.rb has (a) every row of the B2 vector table, (b) every pattern of the B2 refused list (compile returns nil and a reason), (c) the four emitter checks of B2 against the Ruby column, (d) subjects: an invalid UTF-8 string and a binary-encoded string with invalid bytes return false. Run `bundle exec rake test` -> fails: regex.rb does not exist.

Implement the three stages of B2 with the Ruby column: Regex.compile(pattern) returns [matcher, nil] or [nil, reason]; matcher.test(subject) uses Regexp#match? on a UTF-8 subject (a subject in another encoding is transcoded to UTF-8 when possible; invalid encoding and encoding errors give false). Remove U+D800 to U+DFFF from every set. Call Regexp.new(source) with no options. Never emit the caret or the dollar sign: Ruby treats them as line anchors. A RegexpError on the emitted source becomes a compile error.

Verify: the L5 check command passes.

Commit: `feat(sdk-ruby): portable regex translator (MF-256)`

### 35. Ruby: ruleset loader, evaluation, conformance at 100 % and the p99 guard
Read B3, B4 and L5. Files: lib/megooflags/internal/engine/ruleset.rb (the model), parser.rb, evaluator.rb; test/engine/parser_test.rb, evaluator_test.rb; test/conformance_test.rb; test/support/ruleset_gen.rb; perf/p99_test.rb; the Rakefile task p99.

Test first: parser_test.rb and evaluator_test.rb with the B3 test list; conformance_test.rb per B4 (it defines one Minitest method per <stem>::<name> and per bucketing::<input>, reading ../../spec/conformance relative to sdks/ruby); perf/p99_test.rb per B4 with Process.clock_gettime(Process::CLOCK_MONOTONIC, :nanosecond). Run `bundle exec rake test` -> fails: parser.rb and evaluator.rb do not exist.

Implement Parser.parse(text_or_tree) (raises ParseError with the JSON path; warnings on the ruleset) and Evaluator.evaluate(ruleset, flag_key, context, default, expected_type) per B3. The context is valid when it is a Hash with string keys; an integer is an Integer (1.0 is not); frozen model objects; Hash and Set lookups.

Verify: `bundle exec rake test` runs the conformance cases at 100 % (the step 1 cases included); `bundle exec rake p99` prints p50, p99 and max and passes; the L5 check command passes.

Commit: `feat(sdk-ruby): ruleset loader, evaluation and conformance runner (MF-256)`

### 36. Ruby: transport, store, cache and SSE reader
Read B5, B6 (Store, Cache, SSE reader) and L5. Files: lib/megooflags/internal/log.rb, internal/transport/transport.rb, backoff.rb, internal/delivery/store.rb, cache.rb, sse.rb; test/support/fake_server.rb; test/transport/transport_test.rb, backoff_test.rb, test/delivery/store_test.rb, cache_test.rb, sse_test.rb.

Test first: the B5 test list and the B6 test lists for Store, Cache and the SSE reader. fake_server.rb is a small HTTP/1.1 server on TCPServer bound to 127.0.0.1 port 0, one thread per connection: scripted responses, a held-open chunked SSE response the test writes to, a request log, stop. Run `bundle exec rake test` -> fails: files not found.

Implement: Transport.headers, status_error with the errors FatalError, RetryAfterError and HttpError, parse_retry_after (Time.httpdate for the HTTP-date form), Backoff with an injectable random source, a request helper on Net::HTTP with open and read timeouts; Store (one instance variable holding a frozen snapshot); Cache (Digest::SHA256, a temp file in the cache directory, File.chmod 0600, File.rename); Sse.read(io_or_chunk_enumerator, callbacks) splitting on the LF byte and forcing each line to UTF-8; Log (the L5 default logger with the LoadError fallback, tested by stubbing require).

Verify: the L5 check command passes.

Commit: `feat(sdk-ruby): transport helpers, ruleset store, disk cache and SSE reader (MF-256)`

### 37. Ruby: streamer, poller and sync coordinator
Read B6 (Streamer, Poller, Sync) and L5. Files: lib/megooflags/internal/delivery/streamer.rb, poller.rb, sync.rb; test/delivery/streamer_test.rb, poller_test.rb, sync_test.rb (against the fake server).

Test first: the B6 test lists for Streamer, Poller and Sync, with timings shortened through the internal tuning object. Run `bundle exec rake test` -> fails: files not found.

Implement with Net::HTTP and threads. The stream request uses read_body with read_timeout 45, so Net::ReadTimeout is the dead-connection error. Sleeps wait on a ConditionVariable (or a Queue pop with a timeout) so that stop ends them; stop also closes the open connection, joins the threads with a short timeout and is idempotent. Threads set report_on_exception to false and rescue StandardError in their loops. No callback after stop or after a fatal status.

Verify: the L5 check command passes; the tests leave no extra live thread (assert Thread.list.size in teardown).

Commit: `feat(sdk-ruby): SSE streamer, poller and sync coordinator (MF-256)`

### 38. Ruby: events aggregator and batcher
Read B7 and L5. Files: lib/megooflags/internal/events/aggregator.rb, batcher.rb; test/events/aggregator_test.rb, batcher_test.rb.

Test first: the B7 test lists. Run `bundle exec rake test` -> fails: files not found.

Implement B7: record pushes to a SizedQueue of 10 000 without blocking and counts a drop on ThreadError; one thread owns the aggregator (LRU on an insertion-ordered Hash) and the sender; the body is JSON.generate; shutdown(deadline) and stop as in B7; an injectable clock.

Verify: the L5 check command passes.

Commit: `feat(sdk-ruby): evaluation counts, exposure dedup and background batcher (MF-256)`

### 39. Ruby: provider, config, lifecycle and fork handling
Read B8 and L5. Files: megooflags.gemspec (add the runtime dependency openfeature-sdk; run `bundle install` and commit Gemfile.lock), lib/megooflags/config.rb, lib/megooflags/provider.rb, lib/megooflags/internal/fork_hook.rb, lib/megooflags.rb (requires the public files); test/config_test.rb, provider_test.rb, lifecycle_test.rb, fork_test.rb.

Test first: the B8 test lists through the real OpenFeature API (OpenFeature::SDK.set_provider_and_wait with a domain per test, build_client, the fetch_*_details calls, add_handler for events, shutdown in teardown) and the fake server. fork_test.rb (skipped unless Process.respond_to?(:fork)): after fork the child has a running sync thread and an empty batcher and sends nothing the parent recorded; the child reports through a pipe and ends with exit!. Run `bundle exec rake test` -> fails: provider.rb does not exist.

Implement as in L5: MegooFlags::Provider is duck-typed, includes OpenFeature::SDK::Provider::EventEmitter and answers true to emits_lifecycle_events?. init emits PROVIDER_READY itself when it succeeds; on failure it emits PROVIDER_ERROR (error code PROVIDER_FATAL for an invalid config or a refused key) and re-raises. Results are ResolutionDetails; errors carry the default as value. fetch_integer_value returns an Integer and refuses a non-integral number with TYPE_MISMATCH; fetch_float_value returns a Float; fetch_object_value accepts only a Hash or an Array. ForkHook prepends a module to Process.singleton_class whose _fork calls super and, in the child, restarts every live provider (ObjectSpace::WeakMap) with empty event buffers.

Verify: the L5 check command passes.

Commit: `feat(sdk-ruby): OpenFeature provider, config, lifecycle and fork handling (MF-256)`

### 40. Ruby: end-to-end test, CI jobs and docs
Read B9, B10 and L5. Files: sdks/ruby/e2e/e2e_test.rb, the Rakefile task e2e, sdks/ruby/README.md, .github/workflows/ci.yml (jobs sdk-ruby and sdk-ruby-e2e), sdks/README.md (the Ruby row), CHANGELOG.md.

Test first: e2e/e2e_test.rb implements B9 with Net::HTTP and JSON for the management API and calls skip unless MEGOOFLAGS_E2E_URL and MEGOOFLAGS_API_TOKEN are set. Start a local stack (Final verification) and run `bundle exec rake e2e`: it must pass; break the stream path on purpose once to see it fail, then restore. Without the variables the test is reported as skipped.

CI: job sdk-ruby with the matrix and steps of L5 (working directory sdks/ruby); job sdk-ruby-e2e in the B10 shape with project megooflags-sdk-ruby, Ruby 3.4 with bundler-cache, and `bundle exec rake e2e` in sdks/ruby.

Docs: README.md with the B10 section list (install with `gem install megooflags` or a Gemfile line, Ruby 3.4 or later, a quick start with OpenFeature::SDK, a paragraph on forking servers such as Puma, Unicorn and Passenger, and the logger option). sdks/README.md row and CHANGELOG.md line as in B10 (tags sdk-ruby/vX.Y.Z).

Verify: the L5 check command; `bundle exec rake p99`; the e2e run; actionlint prints nothing.

Commit: `feat(sdk-ruby): end-to-end test, CI jobs and docs (MF-256)`

### 41. PHP: Composer scaffold and engine primitives
Read B1 and L6. Everything is under sdks/php.

Create: composer.json (L6; open-feature/sdk is added in step 46), composer.lock (`composer update`), phpunit.xml.dist (test suites unit, p99 and e2e), phpstan.neon.dist, .php-cs-fixer.dist.php, .gitignore, .gitattributes, LICENSE (a copy of the root LICENSE), src/Version.php, src/Internal/Engine/Values.php, Bucketing.php, Semver.php, Dates.php; tests/VersionTest.php, tests/Engine/ValuesTest.php, BucketingTest.php, SemverTest.php, DatesTest.php.

Test first: the engine tests with every B1 vector, on values decoded with json_decode($text, false); VersionTest: Version::VERSION is 0.1.0. Run `composer test` -> fails: the classes do not exist.

Implement B1 as final classes with static methods under MegooFlags\Internal\Engine. A number is is_int or is_float, never a bool; two ints compare exactly, anything else as floats. Use hash('sha256', $text, true) and the byte-wise modulo (PHP has no unsigned 64-bit integer). Every file declares strict_types.

Verify: the L6 check command passes.

Commit: `feat(sdk-php): Composer scaffold and engine primitives (MF-256)`

### 42. PHP: portable regex translator
Read B2 and L6. Files: src/Internal/Engine/Regex.php, tests/Engine/RegexTest.php.

Test first: RegexTest has (a) every row of the B2 vector table, (b) every pattern of the B2 refused list (translate returns null and sets a reason), (c) the four emitter checks of B2 against the PHP column, (d) a subject that is not valid UTF-8 returns false. Run `composer test` -> fails: Regex does not exist.

Implement the three stages of B2 with the PHP column: Regex::translate(string $pattern, ?string &$error = null): ?string returns the complete PCRE pattern (delimiter, the leading UTF verb, the emitted source) or null; Regex::test(string $pcre, string $subject): bool returns true only when the subject is valid UTF-8 (a match of the empty pattern with the u modifier returns 1) and preg_match returns 1. Never add the modifiers i, m, s or u to the translated pattern. Remove U+D800 to U+DFFF from every set. Iterate the pattern by code point: split it with preg_split and the u modifier, and refuse a pattern that is not valid UTF-8. A preg error on the translated pattern (checked once at translate time against an empty subject) becomes a compile error.

Verify: the L6 check command passes.

Commit: `feat(sdk-php): portable regex translator (MF-256)`

### 43. PHP: ruleset compiler, evaluation, conformance at 100 % and the p99 guard
Read B3, B4 and L6 (JSON in the engine, Compiled ruleset). Files: src/Internal/Engine/Compiler.php, ParseException.php, Evaluator.php, Result.php; tests/Engine/CompilerTest.php, EvaluatorTest.php, tests/ConformanceTest.php, tests/Support/RulesetGen.php, tests/Perf/P99Test.php.

Test first: CompilerTest and EvaluatorTest with the B3 test list (the loader is Compiler::compile); one more CompilerTest: var_export of a compiled ruleset, written to a file and loaded with require, equals the compiled array, and it contains no object. ConformanceTest per B4 with a data provider keyed <stem>::<name> and bucketing::<input>, reading ../../spec/conformance relative to sdks/php; it decodes with objects so that an empty object differs from an empty array. P99Test per B4 with hrtime(true). Run `composer test` -> fails: the classes do not exist.

Implement Compiler::compile(mixed $tree): array (the B3 checks; ParseException with the JSON path; the L6 array shape; matches values are translated with Regex::translate, failures go to warnings) and Compiler::compileJson(string $json). Implement Evaluator (constructed with a flag that says whether composite values are decoded to arrays or to objects): evaluate(array $ruleset, string $flagKey, mixed $context, mixed $default, ?string $expectedType): Result per B3. The context is valid when it is a stdClass; an array attribute is a list (array_is_list); stdClass and non-list arrays are objects and match nothing.

Verify: `composer test` runs the conformance cases at 100 % (the step 1 cases included); `composer p99` prints p50, p99 and max and passes; the L6 check command passes.

Commit: `feat(sdk-php): ruleset compiler, evaluation and conformance runner (MF-256)`

### 44. PHP: HTTP transport and cache files
Read B5, B6 (Cache) and L6 (Cache directory files, Fake server). Files: src/Internal/Transport/Http.php, Response.php, Backoff.php; src/Internal/Cache/CacheFiles.php, State.php, Lock.php; tests/Support/FakeServer.php, router.php; tests/Transport/HttpTest.php, BackoffTest.php; tests/Cache/CacheFilesTest.php, StateTest.php, LockTest.php.

Test first: the B5 test list against the fake server (headers, status mapping, Retry-After, backoff, timeout). CacheFilesTest: the file names use the first 16 hex characters of sha256 of the key and never contain the key; the JSON cache round trip is the B6 format; the compiled file round trip through require; a network save writes both files and a bootstrap save writes only the compiled file with an empty etag; a corrupt or truncated JSON or compiled file is ignored and returns null; writes leave no temp file; mode 0600. StateTest: defaults when the file is missing or corrupt; atomic write; the fatal marker. LockTest: the second non-blocking acquire fails until release. Run `composer test` -> fails: the classes do not exist.

Implement Http on ext-curl: get and post with the B5 headers, a timeout argument, the status mapping (fatal, retry-after seconds, HTTP status) and Retry-After parsing; Backoff::delay(int $failures, float $base, float $max, float $rand); CacheFiles, State and Lock as in L6. Loading the compiled file catches Throwable (a half-written file must not break the request).

Verify: the L6 check command passes.

Commit: `feat(sdk-php): HTTP transport, cache files, state and lock (MF-256)`

### 45. PHP: event recorder, spool, sender and request upkeep
Read B7 and L6 (Request end, Upkeep::run). Files: src/Internal/Events/Recorder.php, Spool.php, Sender.php; src/Internal/Upkeep.php; tests/Events/RecorderTest.php, SpoolTest.php, SenderTest.php; tests/UpkeepTest.php.

Test first. RecorderTest: counts per flag, variant and UTC hour; a null variant for unserved evaluations; one exposure per context key, flag, variant and hour inside the request; context only with sendContext; the spool line shape of L6. SpoolTest: append adds one line; a line is dropped when the file is 8 MiB or larger; claim renames atomically and a second claim finds nothing; stale claimed files older than 60 s are picked up. SenderTest (fake server): aggregation over several lines; batches of at most 1000 items per list; the B7 body shape; 413 and a body over 1 MB split; 422 drops; 429 and 500 return the unsent remainder with a delay; 401 reports fatal. UpkeepTest (fake server): due only when the state is older than pollInterval, next_attempt has passed and the lock is free; 200 writes both cache files and the state etag; 304 keeps them; 401 writes the fatal marker and later runs do nothing; a 500 raises failures and next_attempt; the unsent remainder is appended back to the spool; the state gets checked_at. Run `composer test` -> fails: the classes do not exist.

Implement as in L6. Sender takes an optional dedup callback, so that the daemon can pass its LRU in step 47.

Verify: the L6 check command passes.

Commit: `feat(sdk-php): event recorder, spool, sender and request upkeep (MF-256)`

### 46. PHP: provider and config
Read B8 and L6 (Config, OpenFeature, Request path, Request end). Files: composer.json (add open-feature/sdk; run `composer update` and commit composer.lock), src/Config.php, src/Provider.php; tests/ConfigTest.php, tests/ProviderTest.php.

Test first. ConfigTest: the B8 validation and clamps, plus cacheDir is required. ProviderTest, through the real OpenFeature API (OpenFeatureAPI::getInstance()->setProvider, getClient, the typed get*Details calls) with temp cache directories and the fake server: boolean, string, integer (integral and not), float (an integer value is returned as float), object (arrays; a scalar is TYPE_MISMATCH); flag not found; type mismatch; targeting key becomes key; PREREQUISITE_FAILED reason; metadata rule_index and prerequisite; an invalid config or a client key gives the default with PROVIDER_NOT_READY and a message; load order (compiled cache, then JSON cache, then bootstrap file, then the synchronous fetch bounded by initTimeout); a failed cold start sets next_attempt, and the next provider returns PROVIDER_NOT_READY without a request; offline never opens a connection and writes no spool line; a fatal marker with a cached ruleset still serves it and sends nothing; flush() appends one spool line and runs upkeep when it is due; flush() twice appends nothing new; the state etag differing from the compiled etag reloads the compiled file. Run `composer test` -> fails: Provider does not exist.

Implement as in L6. Provider registers flush() with register_shutdown_function on the first evaluation; errors return the caller's default with Reason::ERROR; no I/O per evaluation after the lazy load.

Verify: the L6 check command passes.

Commit: `feat(sdk-php): OpenFeature provider and config (MF-256)`

### 47. PHP: sync daemon (bin/megooflags-sync)
Read B6 (SSE reader, Streamer, Poller, Sync rules) and L6 (Daemon). Files: bin/megooflags-sync (executable), src/Internal/Daemon/Daemon.php, SseParser.php, Lru.php; tests/Daemon/SseParserTest.php, LruTest.php, DaemonTest.php.

Test first. SseParserTest: the B6 SSE reader test list, feeding arbitrary chunk boundaries. LruTest: eviction and refresh. DaemonTest starts the daemon as a child process (proc_open on bin/megooflags-sync with environment variables and a temp cache directory) against the fake server: a put writes both cache files and the state etag; the state checked_at is refreshed, so Upkeep is not due; an invalid put keeps the previous files; two stream failures start polling with If-None-Match, and a reconnect stops it; a spool line written by the test is shipped within the flush interval, and the same exposure in a second line of the same hour is not sent again; 401, and a revoked event, write the fatal marker and the process exits with status 78; a client key exits 78 without a request; --once fetches, ships and exits 0; SIGTERM ships the spool and exits 0 (skipped without pcntl); --help prints the options and exits 0; the key never appears in the output. Timings are shortened through internal environment variables read only by the daemon tests. Run `composer test` -> fails: the classes do not exist.

Implement as in L6: bin/megooflags-sync finds the Composer autoloader (the global $_composer_autoload_path, else ../vendor/autoload.php, else ../../../autoload.php), parses the flags and the environment, and runs Daemon. Daemon is one curl_multi loop with a 1 s select, the per-line 45 s dead timer, the B6 backoff and stop rules, the 5 s state heartbeat and the flush timer with the LRU.

Verify: the L6 check command passes; `php bin/megooflags-sync --help` exits 0.

Commit: `feat(sdk-php): optional sync daemon with SSE, polling fallback and spool shipping (MF-256)`

### 48. PHP: end-to-end test, CI jobs and docs
Read B9, B10 and L6. Files: sdks/php/tests/E2E/EndToEndTest.php, sdks/php/README.md, .github/workflows/ci.yml (jobs sdk-php and sdk-php-e2e), sdks/README.md (the PHP row), CHANGELOG.md.

Test first: EndToEndTest (suite e2e, skipped unless MEGOOFLAGS_E2E_URL and MEGOOFLAGS_API_TOKEN are set) follows B9 with ext-curl for the management API, adapted to PHP. A new Provider object stands for each request. Check 1: the first provider does the cold-start fetch and the flag is true. Start bin/megooflags-sync as a child process on the same cache directory. Check 2: after the toggle, a new provider sees false within 5 s (PHP has no configuration-changed event). Check 3: 20 more evaluations, flush() on each provider, stop the daemon with SIGTERM (or wait one flush interval where pcntl is missing), then the server reports total >= 21 within 15 s. Check 4: a provider with a closed-port base URL and the same cache directory returns false with default true. Check 5 (upkeep without the daemon): toggle the flag on again, set checked_at to 0 in the state file, evaluate and flush() with a provider, then a new provider sees true, and the evaluations total has grown. Start a local stack (Final verification) and run `composer e2e`: it must pass; break the ruleset path on purpose once to see it fail, then restore.

CI: job sdk-php with the matrix and steps of L6 (working directory sdks/php); job sdk-php-e2e in the B10 shape with project megooflags-sdk-php, PHP 8.3 and `composer install --no-interaction --no-progress && composer e2e` in sdks/php.

Docs: README.md with the B10 section list plus two sections, 'The sync daemon' (how to run vendor/bin/megooflags-sync under systemd or a container, its variables, flags and exit status 78) and 'Upkeep without the daemon' (the cache files, what a request does at its end, fastcgi_finish_request, OPcache notes, the known difference in exposure dedup, the compiled cache wins over the bootstrap file, PROVIDER_NOT_READY instead of PROVIDER_FATAL, flush() for long-running workers). sdks/README.md row and CHANGELOG.md line as in B10 (package megooflags/megooflags, tags sdk-php/vX.Y.Z).

Verify: the L6 check command; `composer p99`; the e2e run; actionlint prints nothing.

Commit: `feat(sdk-php): end-to-end test, CI jobs and docs (MF-256)`

### 49. Release workflow: SDK version check script and six sdk-<lang>-release jobs
Read plan section 'Step 49 reference'. Files: scripts/check_sdk_version.py (new), scripts/tests/test_check_sdk_version.py (new), .github/workflows/release.yml, sdks/java/pom.xml (the release profile).

Test first: scripts/tests/test_check_sdk_version.py builds a temp repository per test (a fixture that writes the six manifests with a given version) and calls main() with --root. Tests: each SDK reads its version (six cases); a matching tag passes for a release tag and for an rc tag; a tag for another version fails; a tag with the wrong prefix (a platform tag, another SDK's tag, the Go form) fails; a missing manifest fails; a manifest version that is not X.Y.Z fails; an unknown --sdk is an argparse error; the success line is printed on stdout and errors on stderr. Run from the repository root: `uv run --frozen --project api pytest scripts/tests/test_check_sdk_version.py -q` -> fails: the module does not exist.

Implement the script as specified (stdlib only: argparse, json, tomllib, re, xml.etree). Run it for real: `python3 scripts/check_sdk_version.py --sdk <lang>` prints 0.1.0 for all six SDKs.

release.yml: add the twelve tag patterns and the six jobs of the reference table. Publishing steps run only with `vars.SDK_PUBLISH_<LANG> == 'true'` and a tag without -rc. Secrets and variables are passed through env, never interpolated into a run script. Add the release profile to sdks/java/pom.xml and check that `mvn -B -ntp -Prelease -Dgpg.skip=true package` builds the jar, the sources jar and the javadoc jar (fix Javadoc warnings on the two public classes if it fails).

Verify: the pytest command passes; from api/: `uv run ruff check ../scripts && uv run ruff format --check ../scripts`; the build command of every row of the table produces its artifact locally (pnpm pack, uv build, mvn package, dotnet pack, gem build, composer archive); actionlint prints nothing. Do not push any tag and do not publish.

Commit: `ci: SDK release jobs with tag checks and opt-in publishing (MF-256)`

### 50. Docs: sdks/RELEASING.md and AGENTS.md local checks
Read plan section 'Step 50 reference'. Files: sdks/RELEASING.md (new), AGENTS.md (sections 9 and 11), sdks/README.md (a link to RELEASING.md under 'Versioning and tags').

This step is documentation; there is no failing test to write. First run every command you are about to document, from a clean checkout state, and copy them exactly: the check, p99 and e2e commands of L1 to L6, and the Go commands from sdks/go/README.md.

Write sdks/RELEASING.md with the four sections of the reference: how an SDK release works, the one-time setup per registry (npm, PyPI, Maven Central, NuGet, RubyGems, Packagist) with the exact secret and variable names used in release.yml, the six SDK_PUBLISH_<LANG> repository variables, and the Go flow. Keep it to what a maintainer must do; plain sentences, one step per list item.

AGENTS.md section 9: after the existing command block add a block 'SDK checks' with one group per SDK (directory, check command, p99 command, e2e command) and the commands that start the stack for the e2e tests (Final verification of this plan). Section 11: add that releases and registry setup are described in sdks/RELEASING.md and that the six SDKs use the tags sdk-<lang>/vX.Y.Z.

Final checks for the whole task: sdks/README.md has six rows with version 0.1.0 (Alpha), 100 % conformance and both CI jobs, and the planned order of step 2; CHANGELOG.md has six new lines under Unreleased / Added; `git grep -n mf_server_ -- sdks` shows only docs, tests and the prefix checks; `git status` is clean of build outputs; run the plan's Final verification block.

Commit: `docs: SDK release guide and local SDK checks in AGENTS.md (MF-256)`

## Final verification
```bash
# shared
(cd api && uv run pytest tests/test_conformance.py -q)
uv run --frozen --project api pytest scripts/tests -q
(cd sdks/go && go test -count=1 -run 'TestConformance' ./...)
docker run --rm -v "$PWD:/repo" -w /repo rhysd/actionlint:latest

# each SDK: check command and p99 guard
(cd sdks/node && pnpm install --frozen-lockfile && pnpm format:check && pnpm lint && pnpm typecheck && pnpm test && pnpm test:p99 && pnpm build && pnpm pack)
(cd sdks/python && uv sync --frozen && uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest && uv run pytest perf -s && uv build)
(cd sdks/java && mvn -B -ntp verify && mvn -B -ntp test -Pp99)
(cd sdks/dotnet && dotnet format --verify-no-changes && dotnet build -c Release && dotnet test -c Release --no-build --filter "Category!=P99&Category!=E2E" && dotnet test -c Release --no-build --filter "Category=P99" && dotnet pack src/MegooFlags -c Release --no-build -o out)
(cd sdks/ruby && bundle exec rubocop && bundle exec rake test && bundle exec rake p99 && gem build megooflags.gemspec)
(cd sdks/php && composer validate --strict && composer cs && composer stan && composer test && composer p99)

# end to end, against the compose stack (as the sdk-<lang>-e2e jobs do)
docker build -t megooflags-api:ci api && docker build -t megooflags-web:ci web
export MEGOOFLAGS_SITE_ADDRESS= MEGOOFLAGS_SECRET_KEY=$(openssl rand -hex 32)
docker compose -p megooflags-sdk-e2e -f deploy/docker-compose.yml -f deploy/compose.ci.yml up -d --wait --wait-timeout 180 caddy
export MEGOOFLAGS_E2E_URL=http://localhost:8080
export MEGOOFLAGS_API_TOKEN=$(bash scripts/sdk_e2e_token.sh "$MEGOOFLAGS_E2E_URL")
(cd sdks/node && pnpm e2e)
(cd sdks/python && uv run pytest e2e -v)
(cd sdks/java && mvn -B -ntp test -Pe2e)
(cd sdks/dotnet && dotnet test -c Release --filter "Category=E2E")
(cd sdks/ruby && bundle exec rake e2e)
(cd sdks/php && composer e2e)
(cd sdks/go && go test -tags e2e -count=1 -v ./e2e)
docker compose -p megooflags-sdk-e2e -f deploy/docker-compose.yml -f deploy/compose.ci.yml down -v
```
Expected: everything passes; each conformance runner reports every `<stem>::<name>` (the 27 new regex cases included) as passed; each p99 printed is under 1 ms; in CI the jobs `sdk-<lang>` and `sdk-<lang>-e2e` are green for the six languages and `sdk-go`, `sdk-go-e2e` stay green.
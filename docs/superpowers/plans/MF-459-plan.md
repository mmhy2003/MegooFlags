# MF-459 implementation plan: remote evaluation, propagation, single-node footprint, stream sizing

## Goal

Make the remote evaluation path lean and make ruleset rebuilds after a change touch only the changed flags. Then re-run `scripts/perf.sh full` so that every NFR row in `docs/ga-checklist.md` section 2 passes. The 100k-streams row passes on the documented sizing. F-6 becomes done.

## Architecture

- **Hot path.** On a cache hit, the SDK and OFREP routes no longer open an `AsyncSession`. Results become plain dicts encoded with orjson. Sorted flag keys are computed once per ruleset. Prometheus label children are looked up once.
- **Incremental rebuild.** `RulesetCache` entries keep each flag's encoded JSON fragment and its `flag_environment.version`. When the fingerprint shows that only versions moved, the cache reloads just those flags with the same queries, filtered by id. It re-parses them, patches the fragments and reassembles the body. The body, the ETag and the parsed ruleset are identical to a full build.
- **Harness.** `scripts/perf.sh` gains `MF_PERF_ONLY` (selected sections, written to the git-ignored scratch file `perf/results/only.json`) and `MF_PERF_PROFILE=1` (a py-spy sidecar sharing the api PID namespace). The stream scenario is gated on at least 8 workers at 80 % of the measured capacity.

## Conventions for every step

- Work on the task branch. Each commit touches only the step's files and never `.megoocolab/`. Commit messages end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- API tests: `megoocolab-runner services run api -- bash -c 'cd api && uv run pytest <paths> -q'`.
- Script tests: `uv run --frozen --project api pytest scripts/tests -q`.
- Lint gate before each commit: `cd api && uv run ruff check . ../scripts && uv run ruff format --check . ../scripts && uv run mypy`.
- Before any `perf.sh` run, rebuild the images: `docker build -t megooflags-api:ci api && docker build -t megooflags-web:ci web`. perf.sh uses the `:ci` images.
- Every new export (module, class, function) gets a one-line docstring, as in the surrounding code.
- Unchanged and passing in every API step: `tests/test_sdk.py`, `test_ofrep.py`, `test_stream.py`, `test_fanout.py`, `test_delivery.py`, `test_worker_*.py`, `test_conformance.py` and `test_openapi.py` (the `openapi.json` drift check).

## File map

| File | Change |
|---|---|
| `scripts/perf.sh` | `MF_PERF_ONLY`, `MF_PERF_PROFILE`, profile capture, stream sizing |
| `scripts/perf/profile.override.yml` (new) | py-spy sidecar with `pid: "service:api"` |
| `.gitignore` | `perf/results/only.json` |
| `AGENTS.md` | One sentence on `MF_PERF_ONLY` / `MF_PERF_PROFILE` in section 9 |
| `scripts/perf_report.py` | New `streams_100k` gate, row and target text |
| `scripts/tests/test_perf_report.py` | Tests for the gate, the row and the `perf.sh` options |
| `api/src/megooflags_api/services/ruleset_cache.py` | `peek`, `sorted_keys`, `_Parts`, split fingerprint, incremental rebuild |
| `api/src/megooflags_api/services/sdk_keys.py` | `KeyResolver.cached` |
| `api/src/megooflags_api/services/delivery.py` | `DbRulesetSource` and `DbKeySource` ask the caches first |
| `api/src/megooflags_api/evaluation/engine.py` | `evaluate_all(..., sorted_keys=)` |
| `api/src/megooflags_api/evaluation/parse.py`, `evaluation/__init__.py` | Public `parse_flag` |
| `api/src/megooflags_api/responses.py` (new) | orjson `dumps` with stdlib fallback, `FastJSONResponse` |
| `api/src/megooflags_api/routers/sdk.py`, `routers/ofrep.py` | Dict results, `FastJSONResponse`, sorted keys |
| `api/src/megooflags_api/metrics.py`, `logging.py` | Cached label children; access-log level guard |
| `api/src/megooflags_api/services/ruleset.py` | `FlagParts`, `RulesetParts`, `build_flags`, `build_segments`, `build_parts`, `flag_versions` |
| `api/pyproject.toml`, `api/uv.lock` | `orjson` |
| `api/tests/test_ruleset_cache.py`, `test_ruleset_builder.py`, `test_scale.py`, `test_sdk.py`, `test_ofrep.py`, `test_evaluation_engine.py`, `test_evaluation_parse.py`, `test_metrics.py`, `test_logging.py`, `test_responses.py` (new) | Tests |
| `perf/results/0.1.0.json`, `perf/REPORT.md` | The new full run (`VERSION` is still 0.1.0; the old baseline stays in git history) |
| `docs/ga-checklist.md`, `CHANGELOG.md` | Results; F-6 done; blockers 1 to 4 closed |
| KB: "PRD: MegooFlags" | Section 7 Scalability row |

## Steps

### 1. Perf harness: MF_PERF_ONLY, scratch results, py-spy profiling, then the baseline profile

**Test first** (`scripts/tests/test_perf_report.py`):
- `test_perf_sh_rejects_an_unknown_only_section`: run `bash scripts/perf.sh full` with `MF_PERF_ONLY=bogus` (subprocess, 30 s timeout). It returns exit code 2, and stderr contains `unknown section 'bogus'`.
- `test_perf_sh_only_writes_scratch_results`: the script text contains `perf/results/only.json`, and `.gitignore` lists `perf/results/only.json`.
- `test_profile_override_shares_the_api_pid_namespace`: `yaml.safe_load` the override if PyYAML is available, otherwise check the text. Service `pyspy` has `pid: "service:api"` and `SYS_PTRACE` in `cap_add`.

Run `uv run --frozen --project api pytest scripts/tests/test_perf_report.py -q`. Expected: the three new tests fail.

**Implementation** (`scripts/perf.sh`), right after the `case "$profile"` block and before `root=`:

```bash
# MF_PERF_ONLY=<section,...> runs only those sections and writes the git-ignored perf/results/only.json,
# never perf/results/<VERSION>.json. Sections: remote replicas cluster streams footprint local_eval.
only="${MF_PERF_ONLY:-}"
sections="remote replicas cluster streams footprint local_eval"
for s in ${only//,/ }; do
  case " $sections " in
    *" $s "*) ;;
    *) echo "MF_PERF_ONLY: unknown section '$s' (one of: $sections)" >&2; exit 2 ;;
  esac
done
wants() { [ -z "$only" ] || case ",$only," in *",$1,"*) return 0 ;; *) return 1 ;; esac; }
```

Section gating:
- Section 3 starts the stack and seeds when `wants remote || wants replicas`. Its loads and the metas for `remote_ofrep`, `remote_evaluate`, `whole_env_10k` and `flags_10k` run only for `wants remote`.
- Section 4 runs for `wants replicas`. `single down -v --remove-orphans` runs after section 3 or 4, whichever ran last.
- Section 5 starts the control plane, the three workers and the cluster seed when `wants cluster || wants streams`. The `prop-cluster` load and its meta run for `wants cluster`.
- Section 6 runs for `wants streams`. `cluster_down` runs after section 5 or 6.
- Section 7 runs for `wants footprint`. Section 8 and its metas run for `wants local_eval`.
- Merge: when `only` is set, `dest=$root/perf/results/only.json` and the script prints `perf OK ($profile, only $only)`. Otherwise the destination and the `perf OK ($profile)` line stay as they are.

Profiling, with `MF_PERF_PROFILE=1` and `profile_dir=${MF_PERF_PROFILE_DIR:-/tmp/megooflags-perf-profiles}` (create it):
- `single()` adds `-f "$root/scripts/perf/profile.override.yml"` while `profiling=1`. Set `profiling=1` only for sections 3 and 7. Start the sidecar with `single up -d --wait ... caddy pyspy`.
- At the end of section 3, run `single rm -sf pyspy` before section 4 scales the api to 3.
- Add `profile_record <name> <seconds>`, a no-op unless profiling is on. It waits for the binary with `single exec -T pyspy sh -c 'until command -v py-spy >/dev/null; do sleep 1; done'`. Then it runs `single exec -d -T pyspy py-spy record --pid 1 --subprocesses --nonblocking --rate 200 --duration <s> --format raw --output /out/<name>.txt`. PID 1 is `python -m megooflags_api.serve`, so the Uvicorn workers are its subprocesses.
- Call `profile_record` before the `ofrep` and `evaluate` loads (`warmup + duration`) and before the footprint loads (`foot_hold`).
- In profile mode, after the seed, save the 10k ruleset with `curl -fsS -H "Authorization: Bearer $key" "$base/sdk/v1/ruleset" -o "$profile_dir/ruleset.json"`. Before each `single down`, save `single logs --no-color api` to `$profile_dir/<section>_api.log`.

`scripts/perf/profile.override.yml`: see "Profile override" below. Also update the header comment of perf.sh, add the `.gitignore` line and add one sentence to AGENTS.md section 9.

Run the tests again; all of `scripts/tests` passes. Then `bash -n scripts/perf.sh`, `bash scripts/perf.sh tiny` (prints `perf OK (tiny)`) and `MF_PERF_ONLY=remote bash scripts/perf.sh tiny` (prints `perf OK (tiny, only remote)` and writes `perf/results/only.json`).

**Commit:** `perf: MF_PERF_ONLY scratch runs and py-spy profiling in perf.sh (MF-459)`.

**Baseline profile (no commit).** Rebuild the images, then run `MF_PERF_PROFILE=1 MF_PERF_ONLY=remote,footprint bash scripts/perf.sh full`. Read the raw profiles with a scratch script kept outside the repository. Record the following:
- The top inclusive frames per route, in % of samples. Name the share of FastAPI dependency solving, Pydantic validation and dumping, `AsyncSession` creation, `JSONResponse` rendering, the access log, metrics, evaluation, Valkey and the counters.
- Busy samples per worker pid (the balance).
- GC: a scratch script loads `$profile_dir/ruleset.json` with `parse_ruleset`, registers `gc.callbacks` to time collections, and runs 100 000 `evaluate` calls. Record the gen2 count and the longest pause.
- The footprint api log: the `duration_ms` of the toggle route and of the OFREP requests in the second after each toggle.

Post all of this with the p99 values as a comment on MF-459 (`comment_on_task`).

### 2. Serve cached SDK lookups without opening a session

**Tests first:**
- `tests/test_ruleset_cache.py::test_peek_needs_no_session`:
  - With `FakeClock`, `peek` returns `None` before the first lookup.
  - It returns the same object within `max_age`.
  - It returns `None` after `invalidate`.
  - With a sessionmaker and an expired age, it returns the stale entry and starts one background check (`await settle(cache)`).
- `tests/test_sdk.py::test_cached_evaluate_opens_no_session` and `tests/test_ofrep.py::test_cached_evaluate_opens_no_session`:
  1. Warm the caches with one request.
  2. Replace `app.state.sessionmaker` with a counting wrapper (`monkeypatch.setattr(app.state, "sessionmaker", counting)`).
  3. Send a second request inside `count_statements(app.state.engine)`.
  4. Expect a 200 with the same body, 0 sessions and 0 statements.
- `tests/test_sdk_auth.py::test_key_resolver_cached`: `cached()` returns `None` for an unknown hash, the principal within the TTL, and `None` after the TTL or after `forget`.

Run them. Expected: they fail (no `peek` or `cached`; a session is opened).

**Implementation:**
- `RulesetCache.peek(self, environment: EnvRef, max_age: float) -> CachedRuleset | None`. Docstring: "The entry for an SDK read without a session: fresh within max_age, or stale while one background check runs; None when the caller must wait for a check." The body is today's `max_age > 0` head of `get_entry`: `_fresh`, then `_stale` plus `_refresh_in_background` when a sessionmaker is set. `get_entry` calls `peek` for `max_age > 0`.
- `KeyResolver.cached(self, secret_hash: str) -> SdkPrincipal | None`: the fresh entry, else `None`. `resolve_hash` uses it.
- `DbRulesetSource.get_entry`: `hit = cache.peek(environment, SDK_MAX_AGE)`; on a hit, return it. Otherwise open a session as today.
- `DbKeySource.resolve`:
  - `if key_kind(key) is None: return None`
  - `h = hash_key(key)`; return `resolver.cached(h)` when that is set; otherwise open a session and call `resolver.resolve_hash(session, h)`.
  - `resolve_hash` gets the same cache-first logic.

Run the tests: they pass, along with the unchanged list.

**Commit:** `perf(api): answer cached SDK lookups without a database session (MF-459)`.

### 3. Precompute sorted flag keys on CachedRuleset

**Tests first:**
- `tests/test_evaluation_engine.py::test_evaluate_all_with_sorted_keys`: for a ruleset of several flags, `evaluate_all(r, ctx, sorted_keys=tuple(sorted(r.flags)))` equals `evaluate_all(r, ctx)`, and its keys come in the same order. With `flag_keys` given, `sorted_keys` is ignored.
- `tests/test_ruleset_cache.py::test_entry_has_sorted_keys`: `entry.sorted_keys == tuple(sorted(entry.ruleset.flags))`.
- `tests/test_delivery.py::test_from_body_sorted_keys`: the same check for `from_body`.

Expected: they fail.

**Implementation:**
- `evaluate_all(ruleset, context, flag_keys=None, *, sorted_keys: Sequence[str] | None = None)`. When `flag_keys is None and sorted_keys is not None`, it iterates `sorted_keys`; otherwise it sorts as today. Update the docstring.
- `CachedRuleset` gains `sorted_keys: tuple[str, ...]` after `etag`. Set it in `_prepare` and `from_body` with `tuple(sorted(ruleset.flags))`. These are the only constructors, and the worker's `SnapshotStore` uses `from_body`.
- Both routers pass `sorted_keys=entry.sorted_keys`.

Tests pass. **Commit:** `perf(api): sort flag keys once per ruleset (MF-459)`.

### 4. Plain-dict results encoded with orjson

Run `cd api && uv add 'orjson>=3.11'`. This updates `pyproject.toml` and `uv.lock`; commit both.

**Tests first** (`tests/test_responses.py`):
- `test_dumps_matches_the_standard_encoder`: load every file in `spec/conformance/cases/*.json` and `spec/client/*.json`. For the whole document and each top-level item, `json.loads(dumps(v)) == v`. For values that contain no float, the bytes also equal `json.dumps(v, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode()`.
- `test_dumps_falls_back_for_big_integers`: `dumps({"v": 2**64}) == b'{"v":18446744073709551616}'`, and the same for `-(2**70)`.
- `test_dumps_non_ascii`: `dumps({"k": "grün"})` holds the UTF-8 bytes, not `\u` escapes.

Add to `test_sdk.py` and `test_ofrep.py`, unless an existing test already covers it, an exact-body test for one server and one client key: metadata only for server keys, key order, and no `variant` or `errorDetails` when they are null. Expected: the `responses.py` import fails.

**Implementation:** `api/src/megooflags_api/responses.py`, as in "responses.py" below. In `routers/sdk.py`, build dicts as in "SDK result dict" below and return `FastJSONResponse({"results": results}, headers={"ETag": etag})`. In `routers/ofrep.py`, replace `_item` and `_dump` with `_item(key, r, server: bool) -> dict[str, Any]` (see "OFREP item" below). Keep the 404/400 status logic, and use `FastJSONResponse` for both routes. Keep every `response_model=`. Remove the imports that are no longer used. Regenerate the spec and check that it did not change: `uv run --frozen --project api python -m megooflags_api.export_openapi api/openapi.json && git diff --exit-code api/openapi.json`.

Tests pass. **Commit:** `perf(api): build SDK and OFREP results as dicts and encode them with orjson (MF-459)`.

### 5. Cheaper metrics and access-log bookkeeping

**Tests first:**
- `tests/test_metrics.py::test_label_children_are_looked_up_once`: wrap `metrics.REQUESTS.labels` with a counter, send 3 `GET /healthz`. Expect at most 1 `labels` call, and `REGISTRY.get_sample_value("megooflags_http_requests_total", {...})` grown by 3.
- `tests/test_logging.py::test_access_log_skips_work_when_disabled`: set the `megooflags_api.access` logger to WARNING and monkeypatch `megooflags_api.logging._loggable_path` to raise. A request still returns 200. At INFO, the logged fields are unchanged; reuse the existing assertions.

Expected: they fail.

**Implementation:**
- `metrics.py`: a module dict `_CHILDREN: dict[tuple[str, str, int], tuple[Any, Any]]`, filled on the first use of each `(method, route, status)` with `(REQUESTS.labels(...), DURATION.labels(...))`. The middleware calls `.inc()` and `.observe()` on the cached pair.
- `logging.py`: compute `level` first. `if not access_logger.isEnabledFor(level): return` inside the `finally` block, before `_loggable_path` and the `extra` dict are built.

**Commit:** `perf(api): cache metric label children and skip disabled access logs (MF-459)`.

### 6. Split build_ruleset into flag and segment parts with an id filter; public parse_flag

**Tests first:**
- `tests/test_ruleset_builder.py`:
  - `test_build_parts_matches_build_ruleset`: `(await build_parts(s, env)).to_json() == await build_ruleset(s, env)`.
  - `test_build_flags_by_id_equals_the_subset`: for 2 of the demo flags, `build_flags(s, env, ids).flags` equals those two entries of the full build, and `ids`/`versions` hold only them.
  - `test_flag_versions`: `flag_versions` returns `{flag_id: version}` for the live flags only, without archived ones.
- `tests/test_scale.py::test_build_flags_statement_count_is_constant`: for 1 flag of the demo project and 1 of the 10k project, the statement counts are equal and `<= 9`. The existing `<= 14` test is kept.
- `tests/test_evaluation_parse.py::test_parse_flag_equals_parse_ruleset`: `parse_flag(key, raw)` equals `parse_ruleset(doc).flags[key]`, and a bad flag raises `RulesetError` with the path `flags.<key>...`.

Expected: they fail.

**Implementation:**
- `services/ruleset.py`, as in "ruleset.py contract" below. Move today's flag queries into `build_flags`. When `flag_ids` is given, add `Flag.id.in_(ids)` to the flag and variation queries and `FlagEnvironment.flag_id.in_(ids)` to the fe-joined ones (fes, targets, rules, weights, clauses, clause segments, prerequisites). With `None`, use `sqlalchemy.true()`.
- Move the segment queries into `build_segments`. `build_parts` runs the project key query, `build_flags` and `build_segments`: 14 statements. `build_ruleset` returns `(await build_parts(...)).to_json()`, the same output as today.
- `evaluation/parse.py`: `def parse_flag(key: str, value: Any) -> Flag: """One flag of a ruleset, validated as parse_ruleset does."""` returns `_flag(key, value, f"flags.{key}")`. Export it from `evaluation/__init__.py`.

**Commit:** `refactor(api): build ruleset flags by id and parse one flag (MF-459)`.

### 7. Keep encoded fragments in cache entries and split the fingerprint

No behaviour change yet: the full build assembles the body from fragments.

**Tests first** (`tests/test_ruleset_cache.py`):
- `test_entry_body_from_fragments`: on a project whose flag keys sort differently in Postgres and in Python (`a-b`, `a.b`, `a_b`, `ab`, `b`), with a non-ASCII variation value `"grün"` and rule description `"größer"`, `entry.body == json.dumps(await build_ruleset(...), separators=(",", ":"), ensure_ascii=False).encode()`, and the ETag matches.
- `test_fingerprint_has_three_parts`: the stored fingerprint splits on `|` into 3 md5 values; after a toggle only the first changes, after a segment edit only the third.

Update `test_concurrent_misses_build_once` to monkeypatch `ruleset_cache.build_parts` instead of `build_ruleset`. Expected: the new tests fail.

**Implementation** (`services/ruleset_cache.py`), see "Cache parts" below:
- `FINGERPRINT` selects `md5(<fe part>) || '|' || md5(<flag part>) || '|' || md5(<segment part>)` from the same three subqueries; it stays 1 statement.
- `_Parts` dataclass. `_prepare(parts: RulesetParts, fingerprint) -> tuple[CachedRuleset, _Parts]` encodes the fragments and assembles the body. It parses with the module-global `parse_ruleset(parts.to_json())`, so `test_rebuild_does_not_block_event_loop` still patches it.
- `_Entry` gains `parts: _Parts`. `_rebuild` calls `build_parts` and `asyncio.to_thread(_prepare, ...)`. `from_body` is unchanged apart from `sorted_keys`.

All of `test_ruleset_cache.py`, `test_ruleset_endpoint.py`, `test_cluster_snapshots.py` and the unchanged list pass. **Commit:** `perf(api): keep each flag's encoded fragment in the ruleset cache (MF-459)`.

### 8. Rebuild only the changed flags

**Tests first** (`tests/test_ruleset_cache.py`; helper `assert_same_as_full(app, cache, env)`):
- The helper takes `got = await cache.get_entry(s, env, max_age=0)` and `full = await RulesetCache().get_entry(s, env, max_age=0)`, and asserts that `body`, `etag`, `sorted_keys` and `ruleset` are equal. Clauses hold compiled patterns, which compare by pattern and flags.
- `test_write_matches_full_build`, parametrized over `WRITES`: the persistent cache after each write equals a fresh full build.
- `test_incremental_for_version_writes`: spy on `ruleset_cache.build_flags` and `ruleset_cache.build_parts`.
  - For `config_put`, `toggle`, `kill` and `copy`, `build_flags` gets exactly `{color_id}` and `build_parts` is not called.
- `test_full_build_for_structural_writes`: for `flag_create`, `archive`, `variation_patch` and `segment_put`, `build_parts` is called once and `build_flags` is not.
- `test_toggled_twice_between_checks` and `test_toggled_then_archived`: both equal a full build.
- `test_random_writes_match_full_build`:
  - `random.Random(459)`, 40 iterations over the step 7 flags: toggle, kill, config put with a random fallthrough and the `"größer"` rule, or add a target, plus a `segment_put` or `variation_patch` one time in eight.
  - Check after about half of the writes, so that several writes pile up between checks.
- `test_incremental_failure_falls_back`: `build_flags` raises `RuntimeError`. The result equals a full build, and `caplog` holds `incremental ruleset rebuild failed`.
- `test_many_changed_flags_take_the_full_build`: with `INCREMENTAL_MAX_FLAGS` monkeypatched to 1, toggling two flags calls `build_parts`.

Also `tests/test_scale.py::test_toggle_rebuild_is_incremental`: on the 10k project, warm the cache and toggle `flag-00042` in production. The next `get_entry(max_age=0)` runs as many statements as the same toggle on the demo project (constant, 1 + 1 + 9). `build_flags` is called with that one id, and the body equals a full build. No timing assertion.

Expected: they fail.

**Implementation:** see "Incremental rebuild" below:
- `INCREMENTAL_MAX_FLAGS = 256`, `_only_versions_moved(old_fp, new_fp)`, `_incremental` and `_patch`. `_rebuild` tries the incremental path first, when the old entry exists and only the fe part moved. On `None` or an exception (logged with `logger.exception("incremental ruleset rebuild failed; rebuilding in full", extra={"environment_id": environment.id})`), it falls back to the full build.
- The new entry never mutates the old `_Parts`, dicts or `Ruleset`.
- Update the module docstring with one sentence on the incremental path.

**Commit:** `perf(api): rebuild only the changed flags after a version-only change (MF-459)`.

### 9. Re-profile and apply the profile-driven fixes

Rebuild the images and run `MF_PERF_PROFILE=1 MF_PERF_ONLY=remote,replicas,footprint bash scripts/perf.sh full`. Analyse as in step 1 and post the before and after numbers as a task comment.

Apply a fix only when the profile shows it:
- **GC:** apply when gen2 pauses exceed 5 ms or GC takes more than 5 % of samples. In `RulesetCache._rebuild`, after the new entry is stored, call `gc.freeze()`, with a comment that the ruleset objects are acyclic and are freed by refcount. Test `test_rebuild_freezes_gc`: `gc.get_freeze_count() > 0` after a rebuild.
- **Worker imbalance:** apply when one worker's busy samples exceed 1.5× the mean. Do not guess at a fix. Ask the maintainer (`ask_human`) with the per-worker numbers and a proposed change, then stop.
- **Anything else above 5 % of a request:** fix it inside the files the design names (`routers/sdk.py`, `routers/ofrep.py`, `services/delivery.py`, `evaluation/engine.py`, `logging.py`, `metrics.py`), with a test that pins it. If the fix lies outside them, ask first.

If nothing qualifies, say so in the comment and make no commit. **Commit** (if any): `perf(api): <the fix> from the MF-459 profile`.

### 10. Stream sizing: measured capacity, at least 8 workers, new gate

**Tests first** (`scripts/tests/test_perf_report.py`):
- In `PASSING`, `streams_100k` metrics become `{**PROP, "streams_opened": 25600, "streams_planned": 25600, "workers": 8, "capacity_per_worker": 4000, "workers_for_100k": 32, "mem_max_mib": 430.5, "restarts": 0, "oom_killed": 0}`.
- In `test_render_failing_gate`, replace `{"streams_opened": 99999}` with `{"streams_opened": 25599}` and `{"missed": 100001}` with `{"missed": 25601}`. Add `{"workers": 7}` and `{"workers_for_100k": None}`.
- `test_render_streams_row`: the measured cell reads `25600 streams on 8 workers (4000 per worker; 32 workers for 100k), 0 dropped, propagation p95 0.9 s (0 of 512000 updates missed), worker memory up to 430.5 MiB`.
- `test_render_sizing_guidance_uses_the_measured_capacity`: now expects `4000 streams` and `| 100000 | 32 |`.
- `test_perf_sh_plans_at_least_8_workers`: the script contains `streams_planned=` and `workers_for_100k=`, and its full status needs `big_workers -ge 8`.

Expected: they fail.

**Implementation:**
- `perf_report.py`: the `streams_100k` gate becomes `_all(_at_least("workers", 8), _streams_planned, _at_most("streams_dropped", 0), _PROPAGATION, _at_most("mem_max_mib", 512), _at_least("workers_for_100k", 1))`. `_streams_planned(m)` is true when `streams_opened >= streams_planned` and both are numbers.
- NFR label: `100k streams per cluster`. Target text: `100k streams by adding workers: at least 8 workers at 80 % of one worker's capacity (1 vCPU / 512 MiB), none dropped, propagation p95 < 5 s, each worker inside 512 MiB`. Use the new `_measured` text.
- `perf.sh` section 6:
  - `big_workers = min(need, fit)`, at least 1.
  - `big_streams = 100000` when `need <= fit`, else `big_workers * capacity * 8 / 10`.
  - `big_status=full` when `big_workers -ge 8`, else `reduced` with `"$big_streams streams, $big_workers workers"`.
  - `combine` adds `streams_planned=$big_streams` always, and `workers_for_100k=$need` when the capacity is known.
- The tiny profile stays reduced.

**Commit:** `perf: gate 100k streams on measured per-worker capacity and 8+ workers (MF-459)`.

### 11. Verify the gated scenarios, then the full perf run and REPORT.md

1. Rebuild the images. Run `MF_PERF_ONLY=remote,replicas,footprint bash scripts/perf.sh full`, then `python3 scripts/perf_report.py render --results perf/results/only.json --out /tmp/only-report.md`. The rows for OFREP, `/sdk/v1/evaluate`, replicas and the single node must read `pass`.
2. If a row fails, stop. Ask the maintainer (`ask_human`) with the measured numbers, the profile summary and a proposed target. Do not edit any gate in `perf_report.py` without the answer.
3. Run `bash scripts/perf.sh full` (about an hour).
4. If the `streams_100k` p95 fails, first check that mfload is not the bottleneck: its container CPU (`docker stats mf-perf-streams-100k`) must stay below 90 % of a core per Go thread. If mfload is saturated, ask before changing it.
5. Render with `python3 scripts/perf_report.py render --results perf/results/$(cat VERSION).json --out perf/REPORT.md`. Every NFR row must read `pass`; otherwise apply rule 2.

**Commit** `perf/results/0.1.0.json` and `perf/REPORT.md`: `perf: full run after MF-459 meets the NFR targets`.

### 12. GA checklist, CHANGELOG and the PRD target

- `docs/ga-checklist.md`:
  - Section 1: set F-6 to `done` with the new replica and worker p95.
  - Section 2: give every row the new measured values and verdicts. The 100k row gets the new target and `pass`; the worker footprint row gets the per-worker capacity.
  - Section 4: add a bullet on the changed 100k target (PRD wording below). Update "Remote evaluation needs 8 Uvicorn workers" to the worker count of the run.
  - Section 6: remove blockers 1 to 4. If none is left, write "None." and change the verdict line to say the blockers are closed and the maintainer steps in section 5 remain.
  - Backtick only paths that exist.
- `CHANGELOG.md` under `[Unreleased]`:
  - **Changed**: faster remote evaluation, with OFREP and `/sdk/v1/evaluate` p99 and rps at 2000 rps; flag changes reach SDKs across API replicas faster, with the p95.
  - Replace the "Known gap" line with the measured result.
- Knowledge Base: `kb_search("PRD: MegooFlags")`, `kb_read`, then write a new revision. Change only the section 7 Scalability row to: "10k flags per project, 1M evaluation-context users, 100k connected SDK streams per cluster, reached by adding worker nodes. One worker's capacity inside 1 vCPU / 512 MB is measured, scale-out is verified with at least 8 workers at 80 % of that capacity, and the worker count for 100k follows from it."
- Run `uv run --frozen --project api pytest scripts/tests/test_ga_checklist.py -q` and `python3 scripts/check_version.py`.

**Commit:** `docs: MF-459 closes GA blockers 1 to 4 and F-6`.

## Reference code

### Profile override (`scripts/perf/profile.override.yml`)

```yaml
# Profiling (scripts/perf.sh with MF_PERF_PROFILE=1): py-spy in a sidecar that shares the api
# container's PID namespace, so nothing is added to the shipped image. Needs one api replica.
services:
  pyspy:
    image: python:3.12-slim
    pid: "service:api"
    cap_add: [SYS_PTRACE]
    entrypoint: ["sh", "-c", "pip install --no-cache-dir 'py-spy>=0.4,<0.5' && exec sleep infinity"]
    volumes:
      - ${MF_PERF_PROFILE_DIR:-/tmp/megooflags-perf-profiles}:/out
    depends_on: [api]
```

### responses.py

```python
"""JSON responses for the SDK and OFREP routes, encoded with orjson."""

import json
from typing import Any

import orjson
from fastapi.responses import JSONResponse


def dumps(content: Any) -> bytes:
    """orjson bytes, or the standard encoder for what orjson refuses (integers beyond 64 bits)."""
    try:
        return orjson.dumps(content)
    except TypeError:  # orjson.JSONEncodeError is a TypeError
        return json.dumps(
            content, ensure_ascii=False, allow_nan=False, separators=(",", ":")
        ).encode()


class FastJSONResponse(JSONResponse):
    """A JSONResponse rendered with `dumps`."""

    def render(self, content: Any) -> bytes:
        return dumps(content)
```

### SDK result dict (`routers/sdk.py`)

```python
server = principal.kind == SdkKeyKind.SERVER
results: list[dict[str, Any]] = []
for key, r in resolutions.items():
    item: dict[str, Any] = {
        "key": key, "value": r.value, "variant": r.variant, "reason": r.reason,
        "error_code": r.error_code, "error_message": r.error_message,
    }
    if server:
        item["metadata"] = r.metadata
    results.append(item)
return FastJSONResponse({"results": results}, headers={"ETag": etag})
```

### OFREP item (`routers/ofrep.py`)

```python
def _item(key: str, r: Resolution, server: bool) -> dict[str, Any]:
    """One OFREP result; a null `variant` or `errorDetails` is omitted, a null `value` stays."""
    if r.error_code is not None:
        error: dict[str, Any] = {"key": key, "errorCode": r.error_code}
        if r.error_message is not None:
            error["errorDetails"] = r.error_message
        return error
    item: dict[str, Any] = {"key": key, "value": r.value, "reason": r.reason}
    if r.variant is not None:
        item["variant"] = r.variant
    item["metadata"] = r.metadata if server else {}
    return item
```

### ruleset.py contract

```python
@dataclass(frozen=True, slots=True)
class FlagParts:
    """Flags as SDKs download them, ordered by key as in build_ruleset, with ids and versions."""
    flags: dict[str, dict[str, Any]]   # key -> flag JSON
    ids: dict[uuid.UUID, str]          # flag id -> key
    versions: dict[uuid.UUID, int]     # flag id -> flag_environment.version

@dataclass(frozen=True, slots=True)
class RulesetParts:
    """A whole ruleset before it is joined into one document."""
    project: str
    environment: str
    flags: FlagParts
    segments: dict[str, Any]

    def to_json(self) -> dict[str, Any]:
        """The ruleset document of build_ruleset."""
        return {"schema_version": SCHEMA_VERSION, "project": self.project,
                "environment": self.environment, "flags": self.flags.flags,
                "segments": self.segments}

async def build_flags(session, environment: EnvRef,
                      flag_ids: Collection[uuid.UUID] | None = None) -> FlagParts:
    """The flags of `environment`, or only `flag_ids`, in 9 statements."""

async def build_segments(session, environment: EnvRef) -> dict[str, Any]:
    """The segments section in 4 statements."""

async def build_parts(session, environment: EnvRef) -> RulesetParts:
    """The ruleset of `environment` in 14 statements."""

async def build_ruleset(session, environment: EnvRef) -> dict[str, Any]:
    """The ruleset of `environment` in 14 statements."""
    return (await build_parts(session, environment)).to_json()

async def flag_versions(session, environment: EnvRef) -> dict[uuid.UUID, int]:
    """flag_environment.version per live flag of `environment`, in 1 statement."""
    # select(FlagEnvironment.flag_id, FlagEnvironment.version).join(Flag, Flag.id == FlagEnvironment.flag_id)
    #   .where(FlagEnvironment.environment_id == environment.id, Flag.project_id == environment.project_id,
    #          Flag.archived_at.is_(None))
```

### Cache parts (`ruleset_cache.py`)

```python
def _encode(value: Any) -> bytes:
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False).encode()

@dataclass(frozen=True, slots=True)
class _Parts:
    """What an incremental rebuild reuses from the entry it replaces; never mutated."""
    head: bytes                   # {"schema_version":1,"project":..,"environment":..,"flags":{
    order: tuple[str, ...]        # flag keys in body order
    fragments: dict[str, bytes]   # key -> b'"<key>":{...}'
    tail: bytes                   # },"segments":{...}}
    ids: dict[uuid.UUID, str]
    versions: dict[uuid.UUID, int]

def _body(head: bytes, order, fragments, tail: bytes) -> bytes:
    return head + b",".join(fragments[k] for k in order) + tail

def _prepare(parts: RulesetParts, fingerprint: str) -> tuple[CachedRuleset, _Parts]:
    head = (b'{"schema_version":' + _encode(SCHEMA_VERSION) + b',"project":' + _encode(parts.project)
            + b',"environment":' + _encode(parts.environment) + b',"flags":{')
    fragments = {k: _encode(k) + b":" + _encode(v) for k, v in parts.flags.flags.items()}
    tail = b'},"segments":' + _encode(parts.segments) + b"}"
    order = tuple(parts.flags.flags)
    body = _body(head, order, fragments, tail)
    ruleset = parse_ruleset(parts.to_json())
    cached = CachedRuleset(fingerprint, ruleset, body, _etag(body), tuple(sorted(ruleset.flags)))
    return cached, _Parts(head, order, fragments, tail, parts.flags.ids, parts.flags.versions)
```

`SCHEMA_VERSION` comes from `services/ruleset.py`. The bytes equal `json.dumps(to_json())` with the same options, empty flags included (`"flags":{}`).

### Incremental rebuild (`ruleset_cache.py`)

```python
INCREMENTAL_MAX_FLAGS = 256  # beyond this many changed flags, a full build is cheaper

def _only_versions_moved(old: str, new: str) -> bool:
    """Only the flag_environment part of the fingerprint differs."""
    return old.split("|")[1:] == new.split("|")[1:]

async def _incremental(self, session, environment, old: _Entry, fingerprint: str
                       ) -> tuple[CachedRuleset, _Parts] | None:
    versions = await flag_versions(session, environment)
    if versions.keys() != old.parts.versions.keys():
        return None
    changed = [i for i, v in versions.items() if old.parts.versions[i] != v]
    if len(changed) > INCREMENTAL_MAX_FLAGS:
        return None
    if not changed:  # e.g. an archived flag's row moved
        return dataclasses.replace(old.cached, fingerprint=fingerprint), old.parts
    flags = await build_flags(session, environment, changed)
    if set(flags.ids) != set(changed) or not set(flags.flags) <= old.parts.fragments.keys():
        return None  # archived or renamed meanwhile
    return await asyncio.to_thread(_patch, old.cached, old.parts, flags, fingerprint)

def _patch(cached: CachedRuleset, parts: _Parts, changed: FlagParts, fingerprint: str
           ) -> tuple[CachedRuleset, _Parts]:
    fragments = dict(parts.fragments)
    flags = dict(cached.ruleset.flags)  # same order: only existing keys are replaced
    for key, raw in changed.flags.items():
        fragments[key] = _encode(key) + b":" + _encode(raw)
        flags[key] = parse_flag(key, raw)
    body = _body(parts.head, parts.order, fragments, parts.tail)
    ruleset = dataclasses.replace(cached.ruleset, flags=flags)
    new_parts = dataclasses.replace(parts, fragments=fragments,
                                    versions={**parts.versions, **changed.versions})
    return CachedRuleset(fingerprint, ruleset, body, _etag(body), cached.sorted_keys), new_parts
```

The versions stored come from the per-flag load. A commit between the version query and the load therefore leaves a stored fingerprint older than the data, which only causes one more no-op check, as with today's full build. `_rebuild` keeps its lock, epoch and `invalidated_epoch` handling unchanged.

## Final verification

```bash
cd api && uv sync --frozen && uv run ruff check . ../scripts && uv run ruff format --check . ../scripts && uv run mypy
megoocolab-runner services run api -- bash -c 'cd api && uv run pytest -q'
uv run --frozen --project api pytest scripts/tests -q
uv run --frozen --project api python -m megooflags_api.export_openapi api/openapi.json && git diff --exit-code api/openapi.json
python3 scripts/check_version.py
(cd perf && gofmt -l . && go vet ./... && go test -race ./...)
docker build -t megooflags-api:ci api && docker build -t megooflags-web:ci web
bash scripts/smoke.sh http && bash scripts/cluster_test.sh && bash scripts/perf.sh tiny   # prints "perf OK (tiny)"
bash scripts/perf.sh full
python3 scripts/perf_report.py render --results perf/results/$(cat VERSION).json --out perf/REPORT.md
grep -c '| pass |' perf/REPORT.md   # every NFR row passes
```

# MF-459 — Performance: remote evaluation, propagation, single-node footprint, stream sizing

## Goal

Remote evaluation, propagation across API replicas and the single-node bundle meet their PRD targets in a fresh `scripts/perf.sh full` run, and the 100k-streams row passes on a documented sizing. GA blockers 1 to 4 close and F-6 becomes **done**, with no change to what SDKs, streams or the standalone deployment do.

## What the 0.1.0 numbers say

These are deductions from `perf/results/0.1.0.json` and the code. Nothing was run in this stage, so the first build step confirms each one by measurement.

| Row | Likely cause |
|---|---|
| OFREP p99 266 ms | One Uvicorn worker saturates at about 410 rps (4 workers reached 1651 rps), about 2.4 ms of CPU per request. At 2000 rps eight workers are 60 % busy and requests queue. |
| `/sdk/v1/evaluate` p99 29.6 s | The same overhead plus 100 Pydantic result models, `model_dump` and 100 evaluations per request: about 5.6 ms, so eight workers cannot reach 2000 rps at all. |
| Replica propagation p95 5.31 s | After a change every process rebuilds the whole 10 000-flag ruleset (14 queries, encoding, parsing). A cold build takes 4.4 s (the report's SDK start time), which matches the 5.0 s median. Through workers it is 0.62 s because that project has 100 flags. |
| Single node p99 674 ms, 230 missed | A flip every second invalidates the ruleset, and every evaluation request then waits for a full rebuild against a database with 0.4 CPU. mfload counts an update as missed when the next flip arrives first, so "missed" means "slower than 1 s". |

## Decisions

| Decision | Choice | Why, and what was rejected |
|---|---|---|
| 100k streams | Document the sizing and change the target (your answer) | The build machine fits 12 workers; 100k needs 68. Rejected: a larger host, raising per-worker capacity first, a follow-up task. |
| Remote evaluation | Lean hot path on the same stack (your answer) | Smallest change. Rejected: compiling the engine with mypyc, a separate Go evaluation service. |
| Rebuild cost | Rebuild only the changed flags (your answer) | Same bytes and ETag, no behaviour change. Rejected: serving the old ruleset while rebuilding (changes a tested behaviour, does not fix replicas), sharing builds through Valkey. |
| A target still missed after the fixes | The build stops and asks you, with the measured numbers, the profile and a proposed target | Only the 100k target changes with this design. No other gate in `perf_report.py` is edited without your answer. |
| Fan-out, debounce (0.1 s) and re-check interval (30 s) | Unchanged | The fan-out is not the slow part (0.41 s median through workers). The re-check only covers lost messages. |

**New 100k target (PRD section 7, Scalability).** "100k connected SDK streams per cluster, reached by adding worker nodes. One worker's capacity inside 1 vCPU / 512 MB is measured, scale-out is verified with at least 8 workers at 80 % of that capacity, and the worker count for 100k follows from it." Approving this design accepts that wording.

## Architecture

### 1. Measure first (`scripts/perf.sh`, `scripts/perf/`)

- `MF_PERF_ONLY=<scenario,...>` runs chosen scenarios and writes to a scratch results file, never to `perf/results/<VERSION>.json`. A full run stays the only way to produce the committed results.
- A profiling override for the perf stack: py-spy runs from a sidecar that shares the api container's PID namespace. Nothing is added to the shipped image.
- Recorded before any fix: where a request's time goes on both remote scenarios, requests per Uvicorn worker (balance), garbage-collection pauses, and the single-node scenario under load (the toggle request, the rebuild, the waits).

### 2. Lean hot path (`routers/sdk.py`, `routers/ofrep.py`, `services/delivery.py`, `evaluation/engine.py`, `logging.py`, `metrics.py`)

Certain from reading the code:

- **No session objects on a cache hit.** `DbRulesetSource` and `DbKeySource` open an `AsyncSession` per request even when the cache answers. They ask the cache first and open a session only on a miss.
- **Results without Pydantic.** Both routers build plain dicts in the shape the response models describe. The models stay as `response_model`, so `openapi.json` does not change.
- **orjson** for SDK and OFREP response bodies (new dependency in `api/pyproject.toml` and `uv.lock`), with the standard encoder as fallback for values orjson refuses (integers beyond 64 bits).
- **Precomputed sorted flag keys** on the cached ruleset instead of `sorted(set(...))` per request.
- **Cheaper bookkeeping, same output:** metric label children are looked up once per (method, route, status); the access log keeps one line per request with the same fields.

Applied only if the profile shows them: Uvicorn worker imbalance behind Caddy, garbage-collection pauses on the large ruleset (`gc.freeze` after a build), and anything else above 5 % of a request.

### 3. Incremental ruleset rebuild (`services/ruleset.py`, `services/ruleset_cache.py`, `evaluation/parse.py`)

- A cache entry also keeps each flag's encoded JSON fragment, the flag order of the last full build and the encoded segments section. Parsed flags are already kept.
- When the fingerprint changed, the cache compares what moved. If the flags (id, key, `updated_at`) and segments are the same and only `flag_environment.version` moved for some flags, it loads those flags alone with the existing queries limited to their ids, re-encodes and re-parses them (`parse_flag`, a public wrapper of the existing `_flag`), and assembles the body from the fragments.
- Any other change (flag created, archived or edited, variations, segments, more than a few hundred flags at once) uses today's full build.
- Invariant: body, ETag and parsed ruleset are identical to a full build. Replicas and worker snapshots therefore agree whichever path each one took.
- Assembly and hashing stay off the event loop, as today. A new entry never mutates the old one, because requests in flight still hold it.

`StreamHub`, `NodeHub` and the SDK routes get this through the cache without changes.

### 4. Stream sizing (`scripts/perf.sh`, `scripts/perf_report.py`)

- The scenario measures one worker's capacity, then runs as many workers as the host fits at 80 % of capacity. With at least 8 workers it is a full run; with fewer it stays "reduced".
- The gate becomes: at least 8 workers, streams opened at the planned size, none dropped, propagation p95 under 5 s, each worker inside 512 MiB. The row shows streams, workers, capacity per worker and the workers needed for 100k.
- If its p95 (4.56 s in 0.1.0) fails, the build first checks that mfload is not the bottleneck: it decodes every `put` of every stream.

### 5. Re-run and documents

- `bash scripts/perf.sh full` on the build machine, then render `perf/REPORT.md`. The 0.1.0 baseline stays in git history, and the pull request shows before and after.
- `docs/ga-checklist.md`: F-6 done, section 2 numbers, section 4 notes the changed 100k target, section 6 closes blockers 1 to 4.
- PRD in the Knowledge Base: the Scalability row gets the wording above.
- `CHANGELOG.md` under Unreleased: the faster remote evaluation and propagation, and the "Known gap" line replaced by the measured result.

## Data and interfaces

- No endpoint, schema, migration, setting or SDK protocol change. Response bodies keep their fields and values.
- `RulesetCache` gains a lookup that needs no session; `KeyResolver` gains a cached lookup. `CachedRuleset` gains the sorted keys.
- `perf.sh` gains `MF_PERF_ONLY` and the profiling override; the `streams_100k` scenario gains `workers_for_100k`.

## Error handling and edge cases

- A failure in the incremental path falls back to the full build and is logged. It never serves a partly updated ruleset.
- A change message that arrives during a rebuild still forces a fresh check (existing behaviour and test).
- A flag toggled twice between checks, or toggled and then archived, is covered by comparing against the entry's stored versions, not by counting messages.
- An invalidated entry is still never served stale; requests wait for the now cheap check.
- Worker nodes parse snapshot bodies as before; an old worker and a new control plane exchange the same bytes.

## Testing

- **Incremental equals full:** for every write in `test_ruleset_cache.py`'s `WRITES` table and for random sequences of writes, the body, ETag and parsed ruleset equal a fresh `build_ruleset`. Includes non-ASCII keys and flag order.
- **Cost:** on the 10 000-flag project of `test_scale.py`, a toggle rebuild runs a constant number of statements and loads rows for one flag. No latency gate in CI, as before.
- **Fallback:** flag create, archive, variation edit and segment edit take the full build.
- **Hot path:** a cached evaluation opens no session and runs no statement; orjson output equals the old encoder on the conformance values, with the big-integer fallback covered.
- **Unchanged and passing:** `test_sdk.py`, `test_ofrep.py`, `test_stream.py`, `test_fanout.py`, `test_delivery.py`, `test_worker_*`, `test_conformance.py`, the `openapi.json` drift check.
- **Harness:** `scripts/tests/test_perf_report.py` for the new stream gate and row; `perf.sh tiny` still runs in CI.
- **Acceptance:** the full run, with every NFR row passing or your answer on a changed target.

## Out of scope

- A full-size 100k run and lower memory per stream.
- A compiled engine or a Go evaluation service.
- Faster cold builds (SDK start time is reported, not gated) and patch events on the stream.
- Serving a stale ruleset after a change.
- The version bump to 1.0.0.
# Performance and sizing

MegooFlags 0.1.0 was load tested with the `full` profile on 2026-11-27, on a host with 16 CPUs and 32050 MiB of RAM. `scripts/perf.sh` starts each stack from the shipped compose files inside the CPU and memory limits listed below and drives it with `mfload`; `scripts/perf_report.py` renders this page from the run's results file. Each non-functional requirement of the product has a verdict: pass, fail, reduced (the host was too small, so the scenario ran at the size shown and is not a pass) or not run.

## NFR verdicts

| NFR | Target | Measured | Verdict |
| --- | --- | --- | --- |
| Local evaluation, Go | p99 < 1 ms (the SDK's p99 guard passes) | guard passed, p99 0.0141 ms | pass |
| Local evaluation, Node.js | p99 < 1 ms (the SDK's p99 guard passes) | guard passed, p99 1 ms | pass |
| Local evaluation, Python | p99 < 1 ms (the SDK's p99 guard passes) | guard passed, p99 0.0719 ms | pass |
| Local evaluation, Java | p99 < 1 ms (the SDK's p99 guard passes) | guard passed, p99 0.027 ms | pass |
| Local evaluation, .NET | p99 < 1 ms (the SDK's p99 guard passes) | guard passed | pass |
| Local evaluation, Ruby | p99 < 1 ms (the SDK's p99 guard passes) | guard passed, p99 0.0646 ms | pass |
| Local evaluation, PHP | p99 < 1 ms (the SDK's p99 guard passes) | guard passed, p99 0.0411 ms | pass |
| Remote evaluation, OFREP | p99 < 50 ms, no errors, at 2000 rps per API container (at least 99.5 % reached) | p99 20.4 ms, 0 errors at 2000 rps | pass |
| Remote evaluation, /sdk/v1/evaluate | p99 < 50 ms, no errors, at 2000 rps per API container (at least 99.5 % reached) | p99 35.8 ms, 0 errors at 2000 rps | pass |
| Propagation across API replicas | p95 < 5 s | propagation p95 1.58 s (0 of 600 updates missed) over 20 flips on 30 streams | pass |
| Propagation to worker nodes | p95 < 5 s | propagation p95 0.47 s (0 of 600 updates missed) over 20 flips on 30 streams | pass |
| 100k streams per cluster | 100k streams by adding workers: at least 8 workers at 80 % of one worker's capacity (1 vCPU / 512 MiB), none dropped, propagation p95 < 5 s, each worker inside 512 MiB | 19065 streams on 12 workers (1986 per worker; 63 workers for 100k), 0 dropped, propagation p95 3.91 s (0 of 381300 updates missed), worker memory up to 494.9 MiB | pass |
| Single-node footprint | 2 vCPU / 2 GB: no restart or OOM kill, p95 < 50 ms, p99 < 150 ms, propagation p95 < 5 s | 0 restarts, 0 OOM kills, p95 47.1 ms, p99 141.6 ms at 50 rps, propagation p95 0.32 s (0 of 12000 updates missed), memory up to 512.2 MiB | pass |

## Reported values

These are measured without a target.

| Value | Measured | Run |
| --- | --- | --- |
| Flags in the project | 10000 | full |
| Ruleset size | 4.6 MiB | full |
| SDK start time (one ruleset download) | 5170.4 ms | full |
| Flag list latency | 29.1 ms | full |
| Whole-environment evaluation (every flag in one `/sdk/v1/evaluate` request) | p50 265 ms, p95 1289.1 ms, p99 4263.9 ms, 0 errors at 4 rps | full |

## Host and limits

| Host | |
| --- | --- |
| CPUs | 16 (Intel(R) Xeon(R) CPU E5-4650 v2 @ 2.40GHz) |
| Memory | 32050 MiB |
| Kernel | 6.8.0-149-generic |
| Docker | 29.10.0 |

| Scenario | Run | Limits | Notes |
| --- | --- | --- | --- |
| `remote_ofrep` | full | api: unlimited, api_workers: 8, replicas: 1 | tuned from 4 to 8 uvicorn workers (MF_PERF_API_WORKERS): with 4 it reached 1651 rps at p99 12.7 s |
| `remote_evaluate` | full | api: unlimited, api_workers: 8, replicas: 1 | tuned from 4 to 8 uvicorn workers (MF_PERF_API_WORKERS): with 4 it reached 712 rps at p99 107.5 s |
| `whole_env_10k` | full | api: unlimited, api_workers: 8, replicas: 1 | every flag of the 10000-flag environment in one request, 5 rps for 30 s |
| `flags_10k` | full | api: unlimited | ruleset_download_ms is the SDK start time: one ruleset download |
| `propagation_replicas` | full | api: unlimited, api_workers: 4, replicas: 3 | 30 streams on the 10000-flag project, a flip every 35 s; 4 workers per replica (MF_PERF_REPLICA_API_WORKERS): at 3 x 8 on 16 CPUs p95 was 11.9 s |
| `propagation_cluster` | full | worker: 1 vCPU / 512 MiB (caddy 0.35 / 256M, api 0.65 / 256M) | 30 streams over 3 workers, a flip every 35 s |
| `streams_100k` | full | worker: 1 vCPU / 512 MiB (caddy 0.35 / 256M, api 0.65 / 256M) | sizing: one worker's capacity is measured, then min(workers for 100k, workers the host fits) run at 80 % of it, and at least 8 workers make a full run; mem_max_mib is the most any one worker used; a flip every 30 s through the control plane; limits re-split from caddy 0.2 / 96M, api 0.8 / 416M, where Caddy was OOM-killed at about 1000 streams |
| `footprint_single` | full | caddy: 0.25 / 128M, web: 0.25 / 256M, api: 1.0 / 768M, 2 workers, db: 0.4 / 640M, valkey: 0.1 / 128M | 100 flags, 20 streams, a flip every second and 50 rps for 600 s; giving the api 1.2 CPU (db 0.3) or 3 workers made p99 worse, so the split is unchanged |
| `local_eval_go` | full | - | the SDK's p99 guard on 10 000 flags |
| `local_eval_node` | full | - | the SDK's p99 guard on 10 000 flags |
| `local_eval_python` | full | - | the SDK's p99 guard on 10 000 flags |
| `local_eval_java` | full | - | the SDK's p99 guard on 10 000 flags |
| `local_eval_dotnet` | full | - | the SDK's p99 guard on 10 000 flags |
| `local_eval_ruby` | full | - | the SDK's p99 guard on 10 000 flags |
| `local_eval_php` | full | - | the SDK's p99 guard on 10 000 flags |

## Sizing guidance

- **Single node, 2 vCPU / 2 GB.** Under the small-team load (20 SDK streams, a flag write every second and 50 remote evaluations per second), the whole bundle measured p99 141.6 ms, propagation p95 0.32 s (0 of 12000 updates missed), memory up to 512.2 MiB, 0 restarts. Beyond that load, add API replicas or move to multi-node.
- **One worker node, 1 vCPU / 512 MB,** held 1986 streams before its memory reached 90 % of the limit.
- **Workers for N streams:** plan each worker at 80 % of that capacity, `workers = ceil(N / 1588)`.

| Streams | Workers |
| --- | --- |
| 10000 | 7 |
| 50000 | 32 |
| 100000 | 63 |
| 250000 | 158 |

- **API workers per container.** Remote evaluation ran on one API container with 8 Uvicorn workers (`MEGOOFLAGS_API_WORKERS`) at 2000 rps, p99 20.4 ms. For more load, add API containers.

## How to reproduce

Build the images, run the full profile and render the report. On a host too small for a scenario's full size, that scenario runs reduced.

```sh
docker build -t megooflags-api:ci api && docker build -t megooflags-web:ci web
bash scripts/perf.sh full
python3 scripts/perf_report.py render --results perf/results/$(cat VERSION).json \
  --out perf/REPORT.md
```

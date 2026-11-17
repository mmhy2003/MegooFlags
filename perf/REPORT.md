# Performance and sizing

> **Placeholder until the full run in this release.** These numbers come from the `tiny` profile, which runs every scenario at a small size, so no gate can pass.

MegooFlags 0.1.0 was load tested with the `tiny` profile on 2026-11-17, on a host with 16 CPUs and 32050 MiB of RAM. `scripts/perf.sh` starts each stack from the shipped compose files inside the CPU and memory limits listed below and drives it with `mfload`; `scripts/perf_report.py` renders this page from the run's results file. Each non-functional requirement of the product has a verdict: pass, fail, reduced (the host was too small, so the scenario ran at the size shown and is not a pass) or not run.

## NFR verdicts

| NFR | Target | Measured | Verdict |
| --- | --- | --- | --- |
| Local evaluation, Go | p99 < 1 ms (the SDK's p99 guard passes) | - | not run |
| Local evaluation, Node.js | p99 < 1 ms (the SDK's p99 guard passes) | - | not run |
| Local evaluation, Python | p99 < 1 ms (the SDK's p99 guard passes) | - | not run |
| Local evaluation, Java | p99 < 1 ms (the SDK's p99 guard passes) | - | not run |
| Local evaluation, .NET | p99 < 1 ms (the SDK's p99 guard passes) | - | not run |
| Local evaluation, Ruby | p99 < 1 ms (the SDK's p99 guard passes) | - | not run |
| Local evaluation, PHP | p99 < 1 ms (the SDK's p99 guard passes) | - | not run |
| Remote evaluation, OFREP | p99 < 50 ms, no errors, at 2000 rps per API container | p99 15.3 ms, 0 errors at 50 rps | reduced (tiny: 50 rps for 10 s on 100 flags) |
| Remote evaluation, /sdk/v1/evaluate | p99 < 50 ms, no errors, at 2000 rps per API container | p99 36.5 ms, 0 errors at 50 rps | reduced (tiny: 50 rps for 10 s on 100 flags) |
| Propagation across API replicas | p95 < 5 s | propagation p95 0.91 s over 3 flips on 30 streams | reduced (3 flips) |
| Propagation to worker nodes | p95 < 5 s | propagation p95 0.79 s over 3 flips on 30 streams | reduced (3 flips) |
| 100k streams per cluster | 100000 streams, none dropped, propagation p95 < 5 s, each worker inside 512 MiB | 200 streams, workers: 1, 0 dropped, propagation p95 1.32 s, worker memory up to 155.2 MiB | reduced (tiny: 200 streams, 1 worker) |
| Single-node footprint | 2 vCPU / 2 GB: no restart or OOM kill, p99 < 50 ms, propagation p95 < 5 s | 0 restarts, 0 OOM kills, p99 391.4 ms at 50 rps, propagation p95 0.59 s, memory up to 486.3 MiB | reduced (tiny: 30 s) |

## Reported values

These are measured without a target.

| Value | Measured | Run |
| --- | --- | --- |
| Flags in the project | 100 | reduced (100 flags) |
| Ruleset size | 47.5 KiB | reduced (100 flags) |
| SDK start time (one ruleset download) | 261.5 ms | reduced (100 flags) |
| Flag list latency | 32.5 ms | reduced (100 flags) |
| Whole-environment evaluation (every flag in one `/sdk/v1/evaluate` request) | p50 10.3 ms, p95 18 ms, p99 25.8 ms, 0 errors at 5 rps | reduced (tiny: 100 flags for 5 s) |

## Host and limits

| Host | |
| --- | --- |
| CPUs | 16 (Intel(R) Xeon(R) CPU E5-4650 v2 @ 2.40GHz) |
| Memory | 32050 MiB |
| Kernel | 6.8.0-149-generic |
| Docker | 29.10.0 |

| Scenario | Run | Limits | Notes |
| --- | --- | --- | --- |
| `remote_ofrep` | reduced (tiny: 50 rps for 10 s on 100 flags) | api: unlimited, api_workers: 4, replicas: 1 | - |
| `remote_evaluate` | reduced (tiny: 50 rps for 10 s on 100 flags) | api: unlimited, api_workers: 4, replicas: 1 | - |
| `whole_env_10k` | reduced (tiny: 100 flags for 5 s) | api: unlimited, api_workers: 4, replicas: 1 | every flag of the 100-flag environment in one request, 5 rps for 5 s |
| `flags_10k` | reduced (100 flags) | api: unlimited | ruleset_download_ms is the SDK start time: one ruleset download |
| `propagation_replicas` | reduced (3 flips) | api: unlimited, api_workers: 4, replicas: 3 | 30 streams on the 100-flag project |
| `propagation_cluster` | reduced (3 flips) | worker: 1 vCPU / 512 MiB (caddy 0.2 / 96M, api 0.8 / 416M) | 30 streams over 3 workers |
| `streams_100k` | reduced (tiny: 200 streams, 1 worker) | worker: 1 vCPU / 512 MiB (caddy 0.2 / 96M, api 0.8 / 416M) | mem_max_mib is the most any one worker used; a flip every 5 s through the control plane |
| `footprint_single` | reduced (tiny: 30 s) | caddy: 0.25 / 128M, web: 0.25 / 256M, api: 1.0 / 768M, 2 workers, db: 0.4 / 640M, valkey: 0.1 / 128M | 100 flags, 20 streams, a flip every second and 50 rps for 30 s |
| `local_eval_go` | not run | - | not in the tiny profile |
| `local_eval_node` | not run | - | not in the tiny profile |
| `local_eval_python` | not run | - | not in the tiny profile |
| `local_eval_java` | not run | - | not in the tiny profile |
| `local_eval_dotnet` | not run | - | not in the tiny profile |
| `local_eval_ruby` | not run | - | not in the tiny profile |
| `local_eval_php` | not run | - | not in the tiny profile |

## Sizing guidance

- **Single node, 2 vCPU / 2 GB.** Under the small-team load (20 SDK streams, a flag write every second and 50 remote evaluations per second), the whole bundle measured p99 391.4 ms, propagation p95 0.59 s, memory up to 486.3 MiB, 0 restarts. Beyond that load, add API replicas or move to multi-node.
- **Worker nodes, 1 vCPU / 512 MB.** This run did not measure a worker's stream capacity; the full profile does.
- **API workers per container.** Remote evaluation ran on one API container with 4 Uvicorn workers (`MEGOOFLAGS_API_WORKERS`) at 50 rps, p99 15.3 ms. For more load, add API containers.

## How to reproduce

Build the images, run the full profile and render the report. On a host too small for a scenario's full size, that scenario runs reduced.

```sh
docker build -t megooflags-api:ci api && docker build -t megooflags-web:ci web
bash scripts/perf.sh full
python3 scripts/perf_report.py render --results perf/results/$(cat VERSION).json \
  --out perf/REPORT.md
```

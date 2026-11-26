#!/usr/bin/env python3
"""Load test results (scripts/perf.sh): merge one run's files into perf/results/<version>.json,
and render that file into perf/REPORT.md, which the docs site shows as "Performance and sizing".

`merge --dir <run> --version V --profile P --out FILE` reads from the run directory:

- `host.json`: the machine (`started_at` moves to the top of the document);
- `meta.json`: per scenario, its `status` (full, reduced or skipped), `reduced_to`, `limits` and
  `notes`, as perf.sh decided them;
- `<scenario>.json`: what mfload or local_eval.sh wrote, `{"metrics": {...}, "errors": n}`.

A scenario in `meta.json` without its file is an error, unless it was skipped.

`render --results FILE --out perf/REPORT.md` writes one verdict per NFR of PRD section 7 (`GATES`):
pass, fail, `reduced (<reduced_to>)` when the host was too small for the full size (never a pass)
or not run.
"""

import argparse
import json
import math
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

SCENARIOS = (
    *(f"local_eval_{sdk}" for sdk in ("go", "node", "python", "java", "dotnet", "ruby", "php")),
    "remote_ofrep",
    "remote_evaluate",
    "propagation_replicas",
    "propagation_cluster",
    "flags_10k",
    "whole_env_10k",
    "streams_100k",
    "footprint_single",
)
STATUSES = ("full", "reduced", "skipped")
SDKS = {
    "go": "Go",
    "node": "Node.js",
    "python": "Python",
    "java": "Java",
    "dotnet": ".NET",
    "ruby": "Ruby",
    "php": "PHP",
}


class MergeError(Exception):
    pass


def _read(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise MergeError(f"{path.name} is missing from {path.parent}") from None
    except json.JSONDecodeError as exc:
        raise MergeError(f"{path} is not JSON: {exc}") from None


def _scenario(run: Path, name: str, meta: dict[str, Any]) -> dict[str, Any]:
    if name not in SCENARIOS:
        raise MergeError(f"unknown scenario {name!r} in meta.json")
    status = meta.get("status")
    if status not in STATUSES:
        raise MergeError(f"scenario {name!r} has status {status!r}, expected one of {STATUSES}")
    metrics: dict[str, Any] = {}
    if status != "skipped":
        data = _read(run / f"{name}.json")
        metrics = dict(data.get("metrics") or {})
        metrics.setdefault("errors", data.get("errors", 0))
    return {
        "status": status,
        "reduced_to": meta.get("reduced_to"),
        "limits": meta.get("limits") or {},
        "metrics": metrics,
        "notes": meta.get("notes") or "",
    }


def merge(run: Path, *, version: str, profile: str) -> dict[str, Any]:
    host = dict(_read(run / "host.json"))
    started_at = host.pop("started_at", None)
    meta = _read(run / "meta.json")
    return {
        "version": version,
        "profile": profile,
        "started_at": started_at,
        "host": host,
        "scenarios": {name: _scenario(run, name, m) for name, m in meta.items()},
    }


# Rendering. Each check reads a scenario's metrics; a missing metric fails its gate.
Metrics = dict[str, Any]
Check = Callable[[Metrics], bool]


def _num(m: Metrics, key: str) -> float | None:
    value = m.get(key)
    return float(value) if isinstance(value, int | float) else None


def _below(key: str, limit: float) -> Check:
    def check(m: Metrics) -> bool:
        value = _num(m, key)
        return value is not None and value < limit

    return check


def _at_most(key: str, limit: float) -> Check:
    def check(m: Metrics) -> bool:
        value = _num(m, key)
        return value is not None and value <= limit

    return check


def _at_least(key: str, limit: float) -> Check:
    def check(m: Metrics) -> bool:
        value = _num(m, key)
        return value is not None and value >= limit

    return check


def _all(*checks: Check) -> Check:
    return lambda m: all(check(m) for check in checks)


_NO_ERRORS = _at_most("errors", 0)
# mfload's rate includes the tail of the last responses, so a full 2000 rps run reads 1999.x: the
# maintainer accepted 99.5 % of the requested rate (MF-459).
_REMOTE = _all(_below("p99_ms", 50), _NO_ERRORS, _at_least("rps", 1990))
_P95 = _below("prop_p95_ms", 5000)


def _updates(m: Metrics) -> float | None:
    """Flips times streams: the updates the streams should have shown."""
    flips, streams = _num(m, "flips"), _num(m, "streams_opened")
    return None if flips is None or streams is None else flips * streams


def _few_missed(m: Metrics) -> bool:
    # mfload has no sample for a missed update. With a flip interval of at least its 30 s miss
    # window, a miss took over 30 s, so the p95 over every update is < 5 s only if the sampled p95
    # is and at most 5 % were missed.
    missed, updates = _num(m, "missed"), _updates(m)
    return missed is not None and updates is not None and missed <= 0.05 * updates


_PROPAGATION = _all(_P95, _few_missed)


def _streams_planned(m: Metrics) -> bool:
    """Every planned stream opened."""
    opened, planned = _num(m, "streams_opened"), _num(m, "streams_planned")
    return opened is not None and planned is not None and opened >= planned


_REMOTE_TARGET = "p99 < 50 ms, no errors, at 2000 rps per API container (at least 99.5 % reached)"
_PROPAGATION_TARGET = "p95 < 5 s"

GATES: list[tuple[str, str, str, Check]] = [
    *(
        (
            f"Local evaluation, {label}",
            "p99 < 1 ms (the SDK's p99 guard passes)",
            f"local_eval_{sdk}",
            _all(_at_least("passed", 1), _NO_ERRORS),
        )
        for sdk, label in SDKS.items()
    ),
    ("Remote evaluation, OFREP", _REMOTE_TARGET, "remote_ofrep", _REMOTE),
    ("Remote evaluation, /sdk/v1/evaluate", _REMOTE_TARGET, "remote_evaluate", _REMOTE),
    ("Propagation across API replicas", _PROPAGATION_TARGET, "propagation_replicas", _PROPAGATION),
    ("Propagation to worker nodes", _PROPAGATION_TARGET, "propagation_cluster", _PROPAGATION),
    (
        "100k streams per cluster",
        "100k streams by adding workers: at least 8 workers at 80 % of one worker's capacity "
        "(1 vCPU / 512 MiB), none dropped, propagation p95 < 5 s, each worker inside 512 MiB",
        "streams_100k",
        _all(
            _at_least("workers", 8),
            _streams_planned,
            _at_most("streams_dropped", 0),
            _PROPAGATION,
            _at_most("mem_max_mib", 512),
            _at_least("workers_for_100k", 1),
        ),
    ),
    (
        "Single-node footprint",
        "2 vCPU / 2 GB: no restart or OOM kill, p95 < 50 ms, p99 < 150 ms, propagation p95 < 5 s",
        "footprint_single",
        _all(
            _at_most("restarts", 0),
            _at_most("oom_killed", 0),
            # The maintainer's single-node target (MF-459): one api vCPU shared by two workers.
            _below("p95_ms", 50),
            _below("p99_ms", 150),
            _NO_ERRORS,
            # A flip every second: a missed update may only be superseded by the next one.
            _P95,
        ),
    ),
]


def _n(value: Any, digits: int = 1) -> str:
    if not isinstance(value, int | float):
        return "n/a"
    rounded = round(float(value), digits)
    return str(int(rounded)) if rounded == int(rounded) else str(rounded)


def _s(ms: Any) -> str:
    return f"{_n(ms / 1000, 2)} s" if isinstance(ms, int | float) else "n/a"


def _cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def _missed(m: Metrics) -> str:
    return f"{_n(m.get('missed'))} of {_n(_updates(m))} updates missed"


def _measured(name: str, m: Metrics) -> str:
    if name.startswith("local_eval_"):
        guard = "guard passed" if _num(m, "passed") == 1 else "guard failed"
        return f"{guard}, p99 {_n(m.get('p99_ms'), 4)} ms" if "p99_ms" in m else guard
    if name.startswith("remote_"):
        errors = _n(m.get("errors"))
        return f"p99 {_n(m.get('p99_ms'))} ms, {errors} errors at {_n(m.get('rps'), 0)} rps"
    prop = f"propagation p95 {_s(m.get('prop_p95_ms'))} ({_missed(m)})"
    if name.startswith("propagation_"):
        return f"{prop} over {_n(m.get('flips'))} flips on {_n(m.get('streams_opened'))} streams"
    if name == "streams_100k":
        return (
            f"{_n(m.get('streams_opened'))} streams on {_n(m.get('workers'))} workers "
            f"({_n(m.get('capacity_per_worker'))} per worker; "
            f"{_n(m.get('workers_for_100k'))} workers for 100k), "
            f"{_n(m.get('streams_dropped'))} dropped, {prop}, "
            f"worker memory up to {_n(m.get('mem_max_mib'))} MiB"
        )
    return (
        f"{_n(m.get('restarts'))} restarts, {_n(m.get('oom_killed'))} OOM kills, "
        f"p95 {_n(m.get('p95_ms'))} ms, p99 {_n(m.get('p99_ms'))} ms "
        f"at {_n(m.get('rps'), 0)} rps, {prop}, "
        f"memory up to {_n(m.get('mem_max_mib'))} MiB"
    )


def _ran(scenario: dict[str, Any] | None) -> bool:
    return scenario is not None and scenario.get("status") in ("full", "reduced")


def _run_label(scenario: dict[str, Any]) -> str:
    if scenario.get("status") == "reduced":
        return f"reduced ({scenario.get('reduced_to') or 'smaller than the target'})"
    return str(scenario.get("status"))


def _verdict(scenario: dict[str, Any] | None, check: Check) -> str:
    if scenario is None or not _ran(scenario):
        return "not run"
    if scenario["status"] == "reduced":
        return _run_label(scenario)
    return "pass" if check(scenario.get("metrics") or {}) else "fail"


def _nfr_table(scenarios: dict[str, Any]) -> list[str]:
    lines = ["| NFR | Target | Measured | Verdict |", "| --- | --- | --- | --- |"]
    for nfr, target, name, check in GATES:
        scenario = scenarios.get(name)
        measured = _measured(name, scenario.get("metrics") or {}) if _ran(scenario) else "-"
        verdict = _verdict(scenario, check)
        lines.append(f"| {nfr} | {target} | {_cell(measured)} | {_cell(verdict)} |")
    return lines


def _size(nbytes: Any) -> str:
    if not isinstance(nbytes, int | float):
        return "n/a"
    if nbytes >= 1024 * 1024:
        return f"{_n(nbytes / (1024 * 1024))} MiB"
    return f"{_n(nbytes / 1024)} KiB"


def _reported(scenarios: dict[str, Any]) -> list[str]:
    flags = scenarios.get("flags_10k")
    whole_env = scenarios.get("whole_env_10k")
    lines = ["| Value | Measured | Run |", "| --- | --- | --- |"]
    if _ran(flags):
        assert flags is not None
        m = flags.get("metrics") or {}
        run = _cell(_run_label(flags))
        lines += [
            f"| Flags in the project | {_n(m.get('flags'))} | {run} |",
            f"| Ruleset size | {_size(m.get('ruleset_bytes'))} | {run} |",
            f"| SDK start time (one ruleset download) | {_n(m.get('ruleset_download_ms'))} ms "
            f"| {run} |",
            f"| Flag list latency | {_n(m.get('flag_list_ms'))} ms | {run} |",
        ]
    else:
        lines.append("| Ruleset size, SDK start time, flag list latency | - | not run |")
    whole = "| Whole-environment evaluation (every flag in one `/sdk/v1/evaluate` request) |"
    if _ran(whole_env):
        assert whole_env is not None
        m = whole_env.get("metrics") or {}
        lines.append(
            f"{whole} p50 {_n(m.get('p50_ms'))} ms, p95 {_n(m.get('p95_ms'))} ms, "
            f"p99 {_n(m.get('p99_ms'))} ms, {_n(m.get('errors'))} errors "
            f"at {_n(m.get('rps'), 0)} rps "
            f"| {_cell(_run_label(whole_env))} |"
        )
    else:
        lines.append(f"{whole} - | not run |")
    return lines


def _limits(limits: Any) -> str:
    if not isinstance(limits, dict) or not limits:
        return "-"
    return ", ".join(f"{k}: {v}" for k, v in limits.items())


def _host(doc: dict[str, Any]) -> list[str]:
    host = doc.get("host") or {}
    lines = [
        "| Host | |",
        "| --- | --- |",
        f"| CPUs | {_n(host.get('cpus'))} ({_cell(str(host.get('cpu_model', 'n/a')))}) |",
        f"| Memory | {_n(host.get('memory_mib'))} MiB |",
        f"| Kernel | {_cell(str(host.get('kernel', 'n/a')))} |",
        f"| Docker | {_cell(str(host.get('docker', 'n/a')))} |",
        "",
        "| Scenario | Run | Limits | Notes |",
        "| --- | --- | --- | --- |",
    ]
    for name, scenario in (doc.get("scenarios") or {}).items():
        run = _run_label(scenario) if _ran(scenario) else "not run"
        limits = _cell(_limits(scenario.get("limits")))
        notes = _cell(scenario.get("notes") or "-")
        lines.append(f"| `{name}` | {_cell(run)} | {limits} | {notes} |")
    return lines


def _sizing(scenarios: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    foot = scenarios.get("footprint_single")
    if _ran(foot):
        assert foot is not None
        m = foot.get("metrics") or {}
        lines.append(
            "- **Single node, 2 vCPU / 2 GB.** Under the small-team load "
            f"({_n(m.get('streams_opened'))} SDK streams, a flag write every second and "
            f"{_n(m.get('rps'), 0)} remote evaluations per second), the whole bundle measured "
            f"p99 {_n(m.get('p99_ms'))} ms, propagation p95 {_s(m.get('prop_p95_ms'))} "
            f"({_missed(m)}), "
            f"memory up to {_n(m.get('mem_max_mib'))} MiB, {_n(m.get('restarts'))} restarts. "
            "Beyond that load, add API replicas or move to multi-node."
        )
    else:
        lines.append("- **Single node, 2 vCPU / 2 GB.** Not measured in this run.")
    m = (scenarios.get("streams_100k") or {}).get("metrics") or {}
    capacity = _num(m, "capacity_per_worker")
    if capacity:
        per_worker = max(1, int(0.8 * capacity))
        lines += [
            f"- **One worker node, 1 vCPU / 512 MB,** held {_n(capacity)} streams before its "
            "memory reached 90 % of the limit.",
            "- **Workers for N streams:** plan each worker at 80 % of that capacity, "
            f"`workers = ceil(N / {per_worker})`.",
            "",
            "| Streams | Workers |",
            "| --- | --- |",
            *(
                f"| {streams} | {math.ceil(streams / per_worker)} |"
                for streams in (10000, 50000, 100000, 250000)
            ),
            "",
        ]
    else:
        lines.append(
            "- **Worker nodes, 1 vCPU / 512 MB.** This run did not measure a worker's stream "
            "capacity; the full profile does."
        )
    remote = scenarios.get("remote_ofrep") or {}
    workers = (remote.get("limits") or {}).get("api_workers")
    if _ran(remote) and workers:
        rm = remote.get("metrics") or {}
        lines.append(
            "- **API workers per container.** Remote evaluation ran on one API container with "
            f"{workers} Uvicorn workers (`MEGOOFLAGS_API_WORKERS`) at {_n(rm.get('rps'), 0)} rps, "
            f"p99 {_n(rm.get('p99_ms'))} ms. For more load, add API containers."
        )
    else:
        lines.append("- **API workers per container.** Not measured in this run.")
    return lines


def render(doc: dict[str, Any]) -> str:
    scenarios: dict[str, Any] = doc.get("scenarios") or {}
    host = doc.get("host") or {}
    profile = doc.get("profile")
    started = str(doc.get("started_at") or "an unknown date")
    lines = ["# Performance and sizing", ""]
    if profile != "full":
        lines += [
            "> **Placeholder until the full run in this release.** These numbers come from the "
            f"`{profile}` profile, which runs every scenario at a small size, so no gate can pass.",
            "",
        ]
    lines += [
        f"MegooFlags {doc.get('version')} was load tested with the `{profile}` profile on "
        f"{started[:10]}, on a host with {_n(host.get('cpus'))} CPUs and "
        f"{_n(host.get('memory_mib'))} MiB of RAM. `scripts/perf.sh` starts each stack from the "
        "shipped compose files inside the CPU and memory limits listed below and drives it with "
        "`mfload`; `scripts/perf_report.py` renders this page from the run's results file. Each "
        "non-functional requirement of the product has a verdict: pass, fail, reduced (the host "
        "was too small, so the scenario ran at the size shown and is not a pass) or not run.",
        "",
        "## NFR verdicts",
        "",
        *_nfr_table(scenarios),
        "",
        "## Reported values",
        "",
        "These are measured without a target.",
        "",
        *_reported(scenarios),
        "",
        "## Host and limits",
        "",
        *_host(doc),
        "",
        "## Sizing guidance",
        "",
        *_sizing(scenarios),
        "",
        "## How to reproduce",
        "",
        "Build the images, run the full profile and render the report. On a host too small for "
        "a scenario's full size, that scenario runs reduced.",
        "",
        "```sh",
        "docker build -t megooflags-api:ci api && docker build -t megooflags-web:ci web",
        "bash scripts/perf.sh full",
        "python3 scripts/perf_report.py render --results perf/results/$(cat VERSION).json \\",
        "  --out perf/REPORT.md",
        "```",
        "",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    commands = parser.add_subparsers(dest="command", required=True)
    m = commands.add_parser("merge", help="merge a run directory into one results file")
    m.add_argument("--dir", type=Path, required=True)
    m.add_argument("--version", required=True)
    m.add_argument("--profile", required=True, choices=("full", "tiny"))
    m.add_argument("--out", type=Path, required=True)
    r = commands.add_parser("render", help="render a results file into the Markdown report")
    r.add_argument("--results", type=Path, required=True)
    r.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)

    if args.command == "render":
        report = render(json.loads(args.results.read_text(encoding="utf-8")))
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(report, encoding="utf-8")
        print(f"wrote {args.out}")
        return 0
    try:
        doc = merge(args.dir, version=args.version, profile=args.profile)
    except MergeError as exc:
        print(f"perf_report merge: {exc}", file=sys.stderr)
        return 1
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

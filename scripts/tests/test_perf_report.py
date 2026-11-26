import json
import math
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
from perf_report import GATES, MergeError, main, merge, render

SCRIPTS = Path(__file__).resolve().parents[1]

HOST = {
    "started_at": "2026-11-20T10:00:00Z",
    "cpus": 16,
    "memory_mib": 64215,
    "cpu_model": "Test CPU",
    "kernel": "6.8.0",
    "docker": "29.1.0",
}
OFREP = {
    "metrics": {"rps": 2000, "count": 120000, "errors": 0, "p50_ms": 3.1, "p99_ms": 14.2},
    "errors": 0,
}
STREAMS = {"metrics": {"streams_opened": 30, "prop_p95_ms": 120.0}, "errors": 1}
META = {
    "remote_ofrep": {"status": "full", "reduced_to": None, "limits": {"api": "unlimited"}},
    "propagation_replicas": {
        "status": "reduced",
        "reduced_to": "3 flips",
        "limits": {"api": "unlimited x3"},
        "notes": "tiny profile",
    },
}


def _merge_args(run: Path, profile: str, out: Path) -> list[str]:
    return [
        "merge",
        "--dir",
        str(run),
        "--version",
        "0.1.0",
        "--profile",
        profile,
        "--out",
        str(out),
    ]


def _write(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data), encoding="utf-8")


@pytest.fixture
def results(tmp_path: Path) -> Path:
    d = tmp_path / "run"
    d.mkdir()
    _write(d / "host.json", HOST)
    _write(d / "remote_ofrep.json", OFREP)
    _write(d / "propagation_replicas.json", STREAMS)
    _write(d / "meta.json", META)
    return d


def test_merge_builds_the_document(results: Path) -> None:
    doc = merge(results, version="0.1.0", profile="full")
    assert doc["version"] == "0.1.0"
    assert doc["profile"] == "full"
    assert doc["started_at"] == "2026-11-20T10:00:00Z"
    assert doc["host"] == {k: v for k, v in HOST.items() if k != "started_at"}
    assert doc["scenarios"]["remote_ofrep"] == {
        "status": "full",
        "reduced_to": None,
        "limits": {"api": "unlimited"},
        "metrics": OFREP["metrics"],
        "notes": "",
    }
    replicas = doc["scenarios"]["propagation_replicas"]
    assert replicas["status"] == "reduced"
    assert replicas["reduced_to"] == "3 flips"
    assert replicas["notes"] == "tiny profile"
    # A scenario's error count lands in its metrics when mfload kept it apart.
    assert replicas["metrics"] == {"streams_opened": 30, "prop_p95_ms": 120.0, "errors": 1}
    assert list(doc["scenarios"]) == ["remote_ofrep", "propagation_replicas"]


def test_merge_ignores_extra_keys_in_a_scenario_file(results: Path) -> None:
    # mfload seed writes the SDK keys next to its metrics: they must not reach the results.
    _write(results / "remote_ofrep.json", {**OFREP, "server_key": "mf_server_secret"})
    doc = merge(results, version="0.1.0", profile="full")
    assert "mf_server_secret" not in json.dumps(doc)


def test_missing_scenario_file_is_an_error(results: Path) -> None:
    (results / "propagation_replicas.json").unlink()
    with pytest.raises(MergeError, match="propagation_replicas"):
        merge(results, version="0.1.0", profile="full")


def test_skipped_scenario_needs_no_file(results: Path) -> None:
    meta = {**META, "local_eval_go": {"status": "skipped", "notes": "not in the tiny profile"}}
    _write(results / "meta.json", meta)
    doc = merge(results, version="0.1.0", profile="tiny")
    assert doc["scenarios"]["local_eval_go"] == {
        "status": "skipped",
        "reduced_to": None,
        "limits": {},
        "metrics": {},
        "notes": "not in the tiny profile",
    }


def test_unknown_scenario_is_an_error(results: Path) -> None:
    _write(results / "meta.json", {**META, "remote_ofrepp": {"status": "full"}})
    _write(results / "remote_ofrepp.json", OFREP)
    with pytest.raises(MergeError, match="remote_ofrepp"):
        merge(results, version="0.1.0", profile="full")


def test_unknown_status_is_an_error(results: Path) -> None:
    _write(results / "meta.json", {"remote_ofrep": {"status": "passed"}})
    with pytest.raises(MergeError, match="passed"):
        merge(results, version="0.1.0", profile="full")


def test_missing_host_file_is_an_error(results: Path) -> None:
    (results / "host.json").unlink()
    with pytest.raises(MergeError, match="host.json"):
        merge(results, version="0.1.0", profile="full")


def test_main_writes_the_results_file(results: Path, tmp_path: Path) -> None:
    out = tmp_path / "perf" / "results" / "0.1.0.json"
    code = main(_merge_args(results, "full", out))
    assert code == 0
    doc = json.loads(out.read_text(encoding="utf-8"))
    assert set(doc["scenarios"]) == {"remote_ofrep", "propagation_replicas"}


def test_main_reports_a_merge_error(
    results: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (results / "remote_ofrep.json").unlink()
    out = tmp_path / "out.json"
    code = main(_merge_args(results, "tiny", out))
    assert code == 1
    assert "remote_ofrep" in capsys.readouterr().err
    assert not out.exists()


@pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")
@pytest.mark.parametrize("script", ["perf.sh", "perf/local_eval.sh"])
def test_script_syntax(script: str) -> None:
    subprocess.run(["bash", "-n", str(SCRIPTS / script)], check=True)


def _full(metrics: dict[str, Any], **extra: Any) -> dict[str, Any]:
    return {
        "status": "full",
        "reduced_to": None,
        "limits": {},
        "metrics": metrics,
        "notes": "",
        **extra,
    }


REMOTE = {"rps": 2000.4, "count": 120000, "errors": 0, "p50_ms": 3.1, "p95_ms": 9.0, "p99_ms": 14.2}
PROP = {"streams_opened": 30, "streams_dropped": 0, "flips": 20, "prop_p95_ms": 900.0, "missed": 0}
PASSING = {
    "version": "1.0.0",
    "profile": "full",
    "started_at": "2026-11-20T10:00:00Z",
    "host": {k: v for k, v in HOST.items() if k != "started_at"},
    "scenarios": {
        **{
            f"local_eval_{sdk}": _full({"passed": 1, "p99_ms": 0.02, "errors": 0})
            for sdk in ("go", "node", "python", "java", "dotnet", "ruby", "php")
        },
        "remote_ofrep": _full(REMOTE, limits={"api": "unlimited", "api_workers": "4"}),
        "remote_evaluate": _full(REMOTE),
        "propagation_replicas": _full(PROP),
        "propagation_cluster": _full(PROP),
        "flags_10k": _full(
            {
                "flags": 10000,
                "ruleset_bytes": 4_800_000,
                "ruleset_download_ms": 310.0,
                "flag_list_ms": 42.0,
            }
        ),
        "whole_env_10k": _full(
            {**REMOTE, "rps": 5.0, "count": 150, "p50_ms": 180.0, "p95_ms": 240.0, "p99_ms": 310.5}
        ),
        "streams_100k": _full(
            {
                **PROP,
                "streams_opened": 25600,
                "streams_planned": 25600,
                "workers": 8,
                "capacity_per_worker": 4000,
                "workers_for_100k": 32,
                "mem_max_mib": 430.5,
                "restarts": 0,
                "oom_killed": 0,
            }
        ),
        "footprint_single": _full(
            {**PROP, **REMOTE, "rps": 50.0, "restarts": 0, "oom_killed": 0, "mem_max_mib": 1500.0}
        ),
    },
}


def _with(name: str, **changes: Any) -> dict[str, Any]:
    doc = json.loads(json.dumps(PASSING))
    for key, value in changes.items():
        if key == "metrics":
            doc["scenarios"][name]["metrics"].update(value)
        else:
            doc["scenarios"][name][key] = value
    return doc


def _rows(report: str) -> dict[str, str]:
    """The NFR table: the first cell of each row mapped to its last cell (the verdict)."""
    table = report.split("## NFR verdicts", 1)[1].split("\n## ", 1)[0]
    rows = [line for line in table.splitlines() if line.startswith("| ")][2:]
    cells = [[c.strip() for c in row.strip("|").split("|")] for row in rows]
    return {c[0]: c[-1] for c in cells}


def test_render_starts_with_the_heading() -> None:
    assert render(PASSING).startswith("# Performance and sizing\n")


def test_render_all_gates_pass() -> None:
    rows = _rows(render(PASSING))
    assert len(rows) == len(GATES)
    assert set(rows.values()) == {"pass"}


def test_render_sections_in_order() -> None:
    report = render(PASSING)
    headings = [line for line in report.splitlines() if line.startswith("## ")]
    assert headings == [
        "## NFR verdicts",
        "## Reported values",
        "## Host and limits",
        "## Sizing guidance",
        "## How to reproduce",
    ]
    assert "bash scripts/perf.sh full" in report
    assert "Placeholder" not in report


@pytest.mark.parametrize(
    ("name", "metrics", "row"),
    [
        ("remote_ofrep", {"p99_ms": 50.0}, "Remote evaluation, OFREP"),
        ("remote_ofrep", {"errors": 3}, "Remote evaluation, OFREP"),
        ("remote_ofrep", {"rps": 1500.0}, "Remote evaluation, OFREP"),
        ("remote_evaluate", {"p99_ms": 75.3}, "Remote evaluation, /sdk/v1/evaluate"),
        ("propagation_cluster", {"prop_p95_ms": 5000.0}, "Propagation to worker nodes"),
        ("streams_100k", {"streams_dropped": 1}, "100k streams per cluster"),
        ("streams_100k", {"mem_max_mib": 512.5}, "100k streams per cluster"),
        ("footprint_single", {"oom_killed": 1}, "Single-node footprint"),
        ("footprint_single", {"p99_ms": 695.5}, "Single-node footprint"),
        ("local_eval_java", {"passed": 0}, "Local evaluation, Java"),
        ("local_eval_php", {"errors": 1}, "Local evaluation, PHP"),
        ("propagation_replicas", {"prop_p95_ms": 6000.0}, "Propagation across API replicas"),
        ("streams_100k", {"streams_opened": 25599}, "100k streams per cluster"),
        ("streams_100k", {"workers": 7}, "100k streams per cluster"),
        ("streams_100k", {"workers_for_100k": None}, "100k streams per cluster"),
        ("streams_100k", {"prop_p95_ms": 5000.0}, "100k streams per cluster"),
        ("footprint_single", {"restarts": 1}, "Single-node footprint"),
        ("footprint_single", {"prop_p95_ms": 5000.0}, "Single-node footprint"),
        ("footprint_single", {"errors": 2}, "Single-node footprint"),
        # More than 5 % of the updates (flips x streams) missed: p95 over all of them is not < 5 s.
        ("propagation_replicas", {"missed": 31}, "Propagation across API replicas"),
        ("propagation_cluster", {"missed": 349}, "Propagation to worker nodes"),
        ("streams_100k", {"missed": 25601}, "100k streams per cluster"),
    ],
)
def test_render_failing_gate(name: str, metrics: dict[str, Any], row: str) -> None:
    rows = _rows(render(_with(name, metrics=metrics)))
    assert rows[row] == "fail"
    assert [v for v in rows.values() if v != "pass"] == ["fail"]


def test_render_streams_row() -> None:
    table = render(PASSING).split("## NFR verdicts", 1)[1].split("\n## ", 1)[0]
    row = next(line for line in table.splitlines() if line.startswith("| 100k streams per cluster"))
    measured = [c.strip() for c in row.strip("|").split("|")][2]
    assert measured == (
        "25600 streams on 8 workers (4000 per worker; 32 workers for 100k), 0 dropped, "
        "propagation p95 0.9 s (0 of 512000 updates missed), worker memory up to 430.5 MiB"
    )


def test_render_few_missed_updates_pass() -> None:
    doc = _with("propagation_replicas", metrics={"missed": 30})
    assert _rows(render(doc))["Propagation across API replicas"] == "pass"


def test_render_missed_propagation_metric_fails() -> None:
    doc = _with("propagation_cluster")
    del doc["scenarios"]["propagation_cluster"]["metrics"]["missed"]
    assert _rows(render(doc))["Propagation to worker nodes"] == "fail"


def test_render_shows_missed_updates() -> None:
    doc = _with("propagation_replicas", metrics={"missed": 349})
    doc["scenarios"]["footprint_single"]["metrics"]["missed"] = 121
    report = render(doc)
    nfr = report.split("## Reported values", 1)[0]
    assert "349 of 600 updates missed" in nfr
    # A flip every second: a missed update may only be superseded, so it is shown, not gated.
    assert _rows(report)["Single-node footprint"] == "pass"
    sizing = report.split("## Sizing guidance", 1)[1]
    assert "121 of 600 updates missed" in sizing


def test_render_missing_metric_fails() -> None:
    doc = _with("remote_ofrep")
    del doc["scenarios"]["remote_ofrep"]["metrics"]["p99_ms"]
    assert _rows(render(doc))["Remote evaluation, OFREP"] == "fail"


def test_render_reduced_is_never_pass() -> None:
    doc = _with(
        "streams_100k",
        status="reduced",
        reduced_to="40000 streams, 3 workers",
        metrics={"streams_opened": 40000, "workers": 3},
    )
    report = render(doc)
    assert _rows(report)["100k streams per cluster"] == "reduced (40000 streams, 3 workers)"


def test_render_missing_and_skipped_scenarios_are_not_run() -> None:
    doc = json.loads(json.dumps(PASSING))
    del doc["scenarios"]["propagation_replicas"]
    doc["scenarios"]["local_eval_go"] = {**doc["scenarios"]["local_eval_go"], "status": "skipped"}
    rows = _rows(render(doc))
    assert rows["Propagation across API replicas"] == "not run"
    assert rows["Local evaluation, Go"] == "not run"


def test_render_tiny_profile_is_a_placeholder() -> None:
    report = render({**PASSING, "profile": "tiny"})
    assert report.startswith("# Performance and sizing\n")
    assert "Placeholder until the full run in this release" in report


def _reported_row(report: str, label: str) -> list[str]:
    table = report.split("## Reported values", 1)[1].split("\n## ", 1)[0]
    row = next(line for line in table.splitlines() if line.startswith(f"| {label}"))
    return [c.strip() for c in row.strip("|").split("|")]


def test_render_reports_the_whole_environment_evaluation() -> None:
    row = _reported_row(render(PASSING), "Whole-environment evaluation")
    assert row[1] == "p50 180 ms, p95 240 ms, p99 310.5 ms, 0 errors at 5 rps"
    assert row[2] == "full"


def test_render_whole_environment_evaluation_not_run() -> None:
    doc = json.loads(json.dumps(PASSING))
    del doc["scenarios"]["whole_env_10k"]
    row = _reported_row(render(doc), "Whole-environment evaluation")
    assert row[1:] == ["-", "not run"]


def test_render_sizing_formula_matches_the_table() -> None:
    # 80 % of 31254 is 25003.2: the formula and the table use the same whole number.
    doc = _with("streams_100k", metrics={"capacity_per_worker": 31254})
    sizing = render(doc).split("## Sizing guidance", 1)[1]
    per_worker = int(re.search(r"ceil\(N / (\d+)\)", sizing).group(1))  # type: ignore[union-attr]
    assert per_worker == 25003
    rows = re.findall(r"^\| (\d+) \| (\d+) \|$", sizing, re.MULTILINE)
    assert rows
    for streams, workers in rows:
        assert int(workers) == math.ceil(int(streams) / per_worker)


def test_perf_sh_evaluates_the_whole_10k_environment() -> None:
    script = (SCRIPTS / "perf.sh").read_text(encoding="utf-8").replace("\\\n", " ")
    run = re.search(r"^\s*load whole-env evaluate .*$", script, re.MULTILINE)
    assert run is not None
    assert '--key "$key"' in run.group(0)
    assert "--mode evaluate" in run.group(0)
    assert "/out/whole_env_10k.json" in run.group(0)
    assert re.search(r"^\s*meta whole_env_10k ", script, re.MULTILINE)


def test_render_sizing_guidance_uses_the_measured_capacity() -> None:
    report = render(PASSING)
    sizing = report.split("## Sizing guidance", 1)[1]
    assert "4000 streams" in sizing
    # 100k streams at 80 % of a worker's capacity: ceil(100000 / 3200) workers.
    assert "| 100000 | 32 |" in sizing
    assert "4 Uvicorn workers" in sizing


def test_main_renders_the_report(tmp_path: Path) -> None:
    results = tmp_path / "1.0.0.json"
    results.write_text(json.dumps(PASSING), encoding="utf-8")
    out = tmp_path / "perf" / "REPORT.md"
    assert main(["render", "--results", str(results), "--out", str(out)]) == 0
    assert out.read_text(encoding="utf-8") == render(PASSING)


def test_perf_sh_plans_at_least_8_workers() -> None:
    script = (SCRIPTS / "perf.sh").read_text(encoding="utf-8")
    assert "streams_planned=" in script
    assert "workers_for_100k=" in script
    assert re.search(r'if \[ "\$big_workers" -ge 8 \]; then\s+big_status=full', script)


@pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")
def test_perf_sh_rejects_an_unknown_only_section() -> None:
    done = subprocess.run(
        ["bash", str(SCRIPTS / "perf.sh"), "full"],
        env={**os.environ, "MF_PERF_ONLY": "bogus"},
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert done.returncode == 2
    assert "unknown section 'bogus'" in done.stderr


def test_perf_sh_only_writes_scratch_results() -> None:
    script = (SCRIPTS / "perf.sh").read_text(encoding="utf-8")
    assert "perf/results/only.json" in script
    ignored = (SCRIPTS.parent / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert "perf/results/only.json" in ignored


def test_profile_override_shares_the_api_pid_namespace() -> None:
    text = (SCRIPTS / "perf" / "profile.override.yml").read_text(encoding="utf-8")
    try:
        import yaml
    except ImportError:
        assert 'pid: "service:api"' in text
        assert "SYS_PTRACE" in text
        return
    pyspy = yaml.safe_load(text)["services"]["pyspy"]
    assert pyspy["pid"] == "service:api"
    assert "SYS_PTRACE" in pyspy["cap_add"]

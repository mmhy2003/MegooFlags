import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
from perf_report import MergeError, main, merge

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

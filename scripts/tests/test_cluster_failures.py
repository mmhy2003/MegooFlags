import pytest
from cluster_failures import all_status, eval_total, p95_ok, parse_args

WORKERS = ["--worker", "http://w1", "--worker", "http://w2", "--worker", "http://w3"]
REQUIRED = ["--lb", "https://lb", "--cp", "https://cp", *WORKERS, "--fresh", "http://w4"]


def node(name, status, syncing=False):
    return {"name": name, "status": status, "syncing": syncing}


def test_status_wait_logic():
    names = ["worker-1", "worker-2", "worker-3"]
    nodes = [node("worker-1", "healthy"), node("worker-2", "healthy"), node("worker-3", "healthy")]
    assert all_status(nodes, names, "healthy")
    assert all_status([*nodes, node("worker-4", "pending")], names, "healthy")
    assert not all_status(nodes[:2], names, "healthy")
    assert not all_status([nodes[0], node("worker-2", "stale"), nodes[2]], names, "healthy")
    assert not all_status([nodes[0], node("worker-2", "healthy", True), nodes[2]], names, "healthy")
    assert all_status([node("worker-2", "stale")], ["worker-2"], "stale")
    assert not all_status([node("worker-2", "healthy")], ["worker-2"], "stale")


def test_eval_total():
    series = {
        "interval": "hour",
        "variants": ["", "on"],
        "points": [
            {"start": "2026-11-17T10:00:00Z", "counts": {"": 0, "on": 0}},
            {"start": "2026-11-17T11:00:00Z", "counts": {"": 5, "on": 2}},
            {"start": "2026-11-17T12:00:00Z", "counts": {"": 10, "on": 0}},
        ],
        "total": 17,
    }
    assert eval_total(series) == 17
    assert eval_total({"interval": "hour", "variants": [], "points": [], "total": 0}) == 0


def test_p95_gate():
    assert p95_ok([0.1] * 19 + [9.0])
    assert not p95_ok([0.1] * 18 + [5.0, 9.0])
    assert not p95_ok([5.0])
    assert p95_ok([4.99])
    assert p95_ok([9.0, 1.0], limit=10.0)


def test_arguments(monkeypatch, capsys):
    monkeypatch.delenv("MEGOOFLAGS_API_TOKEN", raising=False)
    args = parse_args([*REQUIRED, "--ca", "ca.crt", "--token", "mf_api_x"])
    assert args.lb == "https://lb"
    assert args.cp == "https://cp"
    assert args.worker == ["http://w1", "http://w2", "http://w3"]
    assert args.fresh == "http://w4"
    assert args.ca == "ca.crt"
    assert args.token == "mf_api_x"
    assert args.ctl == "bash scripts/cluster_test.sh ctl"
    assert parse_args([*REQUIRED, "--token", "t", "--ctl", "echo"]).ctl == "echo"

    monkeypatch.setenv("MEGOOFLAGS_API_TOKEN", "mf_api_env")
    assert parse_args(REQUIRED).token == "mf_api_env"
    assert parse_args(REQUIRED).ca is None

    with pytest.raises(SystemExit):
        parse_args([*REQUIRED[:4], *WORKERS[:4], "--fresh", "http://w4", "--token", "t"])
    assert "--worker" in capsys.readouterr().err

    monkeypatch.delenv("MEGOOFLAGS_API_TOKEN")
    with pytest.raises(SystemExit):
        parse_args(REQUIRED)
    assert "--token" in capsys.readouterr().err

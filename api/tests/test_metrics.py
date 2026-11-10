import json
import os
import subprocess
import sys
from pathlib import Path

import httpx
from conftest import ClientFactory
from prometheus_client import REGISTRY

from megooflags_api.export_openapi import render
from megooflags_api.main import create_app


def sample(name: str, **labels: str) -> float:
    return REGISTRY.get_sample_value(name, labels) or 0.0


async def test_metrics_counts_requests(client_for: ClientFactory) -> None:
    before = sample("megooflags_http_requests_total", method="GET", route="/healthz", status="200")
    async with client_for(None) as client:
        await client.get("/healthz")
        await client.get("/nope/123")
        response = await client.get("/metrics")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    after = sample("megooflags_http_requests_total", method="GET", route="/healthz", status="200")
    assert after == before + 1
    assert sample("megooflags_http_requests_total", method="GET", route="other", status="404") >= 1
    assert "/nope/123" not in response.text
    for name in (
        "megooflags_build_info",
        "megooflags_http_request_duration_seconds_bucket",
        "megooflags_http_requests_in_progress",
    ):
        assert name in response.text


async def test_route_label_is_the_template() -> None:
    app = create_app()

    async def item(item_id: str) -> dict[str, str]:
        return {"id": item_id}

    app.add_api_route("/_items/{item_id}", item)
    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            await client.get("/_items/42")
    finally:
        await app.state.engine.dispose()

    labels = {"method": "GET", "route": "/_items/{item_id}", "status": "200"}
    assert sample("megooflags_http_requests_total", **labels) >= 1
    raw = {"method": "GET", "route": "/_items/42", "status": "200"}
    assert sample("megooflags_http_requests_total", **raw) == 0


def test_metrics_not_in_openapi() -> None:
    assert "/metrics" not in json.loads(render())["paths"]


def run_python(code: str, metrics_dir: Path) -> str:
    env = {**os.environ, "PROMETHEUS_MULTIPROC_DIR": str(metrics_dir)}
    done = subprocess.run(
        [sys.executable, "-c", code], env=env, capture_output=True, text=True, check=True
    )
    return done.stdout


def test_metrics_add_up_across_worker_processes(tmp_path: Path) -> None:
    worker = (
        "from megooflags_api import metrics\n"
        "metrics.RATE_LIMIT_ERRORS.inc()\n"
        "metrics.IN_PROGRESS.inc()\n"
        "metrics.worker_exited()\n"
    )
    run_python(worker, tmp_path)
    run_python(worker, tmp_path)

    text = run_python(
        "from megooflags_api import metrics\nprint(metrics.render().decode())", tmp_path
    )

    assert "megooflags_rate_limit_errors_total 2.0" in text
    assert "megooflags_http_requests_in_progress 0.0" in text  # exited workers count as idle
    assert 'megooflags_build_info{version="' in text

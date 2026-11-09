import json

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

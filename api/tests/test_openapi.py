import json
from pathlib import Path

from megooflags_api import __version__
from megooflags_api.export_openapi import render

API_DIR = Path(__file__).resolve().parents[1]


def test_spec_documents_health_endpoints() -> None:
    spec = json.loads(render())

    assert {"/healthz", "/readyz"} <= spec["paths"].keys()
    assert {"HealthResponse", "ReadyResponse"} <= spec["components"]["schemas"].keys()
    assert spec["info"]["version"] == __version__
    assert "503" in spec["paths"]["/readyz"]["get"]["responses"]


def test_spec_documents_management_api() -> None:
    spec = json.loads(render())
    flag = "/api/projects/{project}/flags/{flag}"

    assert {
        "/api/projects",
        "/api/projects/{project}",
        "/api/projects/{project}/environments",
        "/api/projects/{project}/environments/{env}",
        "/api/projects/{project}/environments/{env}/evaluate",
        "/api/projects/{project}/environments/{env}/sdk-keys",
        "/api/projects/{project}/environments/{env}/sdk-keys/{id}",
        "/api/projects/{project}/flags",
        flag,
        f"{flag}/archive",
        f"{flag}/restore",
        f"{flag}/copy",
        f"{flag}/variations",
        f"{flag}/variations/{{variation}}",
        f"{flag}/environments",
        f"{flag}/environments/{{env}}",
        f"{flag}/environments/{{env}}/toggle",
        f"{flag}/environments/{{env}}/kill",
        "/api/projects/{project}/segments",
        "/api/projects/{project}/segments/{segment}",
    } <= spec["paths"].keys()

    schemas = spec["components"]["schemas"]
    assert "ErrorBody" in schemas
    put_responses = spec["paths"][f"{flag}/environments/{{env}}"]["put"]["responses"]
    for status in ("401", "403", "409", "422"):
        ref = put_responses[status]["content"]["application/json"]["schema"]["$ref"]
        assert ref == "#/components/schemas/ErrorBody"
    assert "from" in schemas["CopyIn"]["properties"]


def test_spec_documents_sdk_api() -> None:
    spec = json.loads(render())

    assert {
        "/sdk/v1/ruleset",
        "/sdk/v1/evaluate",
        "/sdk/v1/stream",
        "/sdk/v1/events",
    } <= spec["paths"].keys()
    assert "413" in spec["paths"]["/sdk/v1/events"]["post"]["responses"]


def test_spec_documents_ofrep() -> None:
    spec = json.loads(render())

    assert {"/ofrep/v1/evaluate/flags", "/ofrep/v1/evaluate/flags/{key}"} <= spec["paths"].keys()


def test_committed_spec_is_current() -> None:
    committed = (API_DIR / "openapi.json").read_text(encoding="utf-8")

    assert committed == render(), (
        "api/openapi.json is stale. "
        "Run `uv run python -m megooflags_api.export_openapi openapi.json` from api/."
    )

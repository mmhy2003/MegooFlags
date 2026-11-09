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


def test_committed_spec_is_current() -> None:
    committed = (API_DIR / "openapi.json").read_text(encoding="utf-8")

    assert committed == render(), (
        "api/openapi.json is stale. "
        "Run `uv run python -m megooflags_api.export_openapi openapi.json` from api/."
    )

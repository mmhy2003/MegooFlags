from pathlib import Path
from typing import Any

import yaml

WORKFLOWS = Path(__file__).resolve().parents[2] / ".github" / "workflows"


def _load(name: str) -> dict[Any, Any]:
    data = yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


def _triggers(workflow: dict[Any, Any]) -> dict[str, Any]:
    # YAML 1.1 reads the bare key `on` as True.
    triggers = workflow.get("on", workflow.get(True))
    assert isinstance(triggers, dict)
    return triggers


def test_ci_has_docs_jobs() -> None:
    jobs = _load("ci.yml")["jobs"]
    for job in ("site", "quickstart", "proxies"):
        assert job in jobs


def test_ci_site_job_runs_site_checks() -> None:
    site = _load("ci.yml")["jobs"]["site"]
    assert site["defaults"]["run"]["working-directory"] == "site"
    runs = [step["run"] for step in site["steps"] if "run" in step]
    for command in (
        "pnpm install --frozen-lockfile",
        "pnpm format:check",
        "pnpm check",
        "pnpm test",
        "pnpm build",
    ):
        assert command in runs


def test_docs_triggers_on_main_and_dispatch() -> None:
    triggers = _triggers(_load("docs.yml"))
    assert triggers["push"]["branches"] == ["main"]
    assert "workflow_dispatch" in triggers


def test_docs_uploads_site_dist() -> None:
    steps = _load("docs.yml")["jobs"]["build"]["steps"]
    uploads = [s for s in steps if s.get("uses", "").startswith("actions/upload-pages-artifact@")]
    assert len(uploads) == 1
    assert uploads[0]["with"]["path"] == "site/dist"


def test_docs_deploys_to_pages() -> None:
    workflow = _load("docs.yml")
    assert workflow["permissions"] == {"contents": "read", "pages": "write", "id-token": "write"}
    deploy = workflow["jobs"]["deploy"]
    assert deploy["needs"] == "build"
    assert deploy["environment"]["name"] == "github-pages"
    steps = [s for s in deploy["steps"] if s.get("uses", "").startswith("actions/deploy-pages@")]
    assert steps[0]["id"] == "deployment"


def test_ci_has_helm_job() -> None:
    helm = _load("ci.yml")["jobs"]["helm"]
    assert helm["needs"] == "images"
    assert helm["env"]["MEGOOFLAGS_REQUIRE_HELM"]
    setup = [s for s in helm["steps"] if s.get("uses") == "azure/setup-helm@v4"]
    assert len(setup) == 1
    assert setup[0]["with"]["version"] == "v3.19.0"
    runs = [step["run"] for step in helm["steps"] if "run" in step]
    for command in (
        "uv run --frozen --project api pytest -v scripts/tests/test_helm_chart.py",
        "bash scripts/helm_test.sh",
    ):
        assert command in runs


def test_release_chart_job() -> None:
    chart = _load("release.yml")["jobs"]["chart"]
    assert chart["needs"] == ["meta", "merge"]
    assert chart["permissions"] == {"contents": "read", "packages": "write"}
    assert chart["env"]["VERSION"] == "${{ needs.meta.outputs.image_version }}"
    setup = [s for s in chart["steps"] if s.get("uses") == "azure/setup-helm@v4"]
    assert len(setup) == 1
    assert setup[0]["with"]["version"] == "v3.19.0"
    runs = "\n".join(step["run"] for step in chart["steps"] if "run" in step)
    assert (
        'helm package deploy/helm/megooflags --version "$VERSION" --app-version "$VERSION"' in runs
    )
    assert "helm push" in runs
    assert "oci://ghcr.io/mmhy2003/charts" in runs
    uploads = [
        s for s in chart["steps"] if s.get("uses", "").startswith("actions/upload-artifact@")
    ]
    assert len(uploads) == 1
    assert uploads[0]["with"]["name"] == "chart"


def test_release_job_attaches_chart() -> None:
    release = _load("release.yml")["jobs"]["release"]
    assert "chart" in release["needs"]
    downloads = [
        s for s in release["steps"] if s.get("uses", "").startswith("actions/download-artifact@")
    ]
    assert "chart" in [s["with"].get("name") for s in downloads]

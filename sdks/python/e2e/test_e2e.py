"""Runs the provider against a running MegooFlags stack. Set MEGOOFLAGS_E2E_URL (for example
http://localhost:8080) and MEGOOFLAGS_API_TOKEN (scripts/sdk_e2e_token.sh prints one) to run it:
`uv run pytest e2e -v`."""

from __future__ import annotations

import json
import logging
import os
import secrets
import socket
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from openfeature import api
from openfeature.evaluation_context import EvaluationContext
from openfeature.event import EventDetails, ProviderEvent

from megooflags import MegooFlagsProvider

BASE = os.environ.get("MEGOOFLAGS_E2E_URL", "").rstrip("/")
TOKEN = os.environ.get("MEGOOFLAGS_API_TOKEN", "")
REQUEST_TIMEOUT = 10.0

pytestmark = pytest.mark.skipif(
    BASE == "" or TOKEN == "", reason="MEGOOFLAGS_E2E_URL and MEGOOFLAGS_API_TOKEN are not set"
)


def call(method: str, path: str, body: Any = None) -> tuple[int, dict[str, Any]]:
    """Call the management API; return the status and the JSON body."""
    headers = {"Authorization": f"Bearer {TOKEN}"}
    data = None
    if body is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(body).encode()
    req = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as res:
            status, text = res.status, res.read()
    except urllib.error.HTTPError as err:
        status, text = err.code, err.read()
    return status, json.loads(text) if text else {}


def must(want: int, method: str, path: str, body: Any = None) -> dict[str, Any]:
    """Call the management API and fail unless the status is want."""
    status, out = call(method, path, body)
    assert status == want, f"{method} {path}: status {status}, want {want}: {out}"
    return out


def until(what: str, check: Callable[[], bool], timeout: float, interval: float = 0.05) -> None:
    """Poll check every interval seconds until it holds or timeout passes."""
    end = time.monotonic() + timeout
    while not check():
        if time.monotonic() > end:
            pytest.fail(f"{what}: not within {timeout:g} s")
        time.sleep(interval)


def closed_port() -> int:
    """A local port nothing listens on."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port: int = s.getsockname()[1]
    return port


@pytest.fixture
def project(tmp_path: Path) -> Iterator[str]:
    p = f"sdkpython-{secrets.token_hex(4)}"
    try:
        must(
            201,
            "POST",
            "/api/projects",
            {"key": p, "name": p, "environments": [{"key": "e2e", "name": "E2E"}]},
        )
        yield p
    finally:
        api.shutdown()
        call("DELETE", f"/api/projects/{p}")


def test_end_to_end(project: str, tmp_path: Path) -> None:
    flag_path = f"/api/projects/{project}/flags/e2e-flag/environments/e2e"
    must(
        201,
        "POST",
        f"/api/projects/{project}/flags",
        {"key": "e2e-flag", "name": "E2E", "type": "boolean"},
    )
    must(200, "POST", f"{flag_path}/toggle", {"enabled": True})
    created = must(
        201,
        "POST",
        f"/api/projects/{project}/environments/e2e/sdk-keys",
        {"name": "python-e2e", "kind": "server"},
    )
    sdk_key = created.get("key")
    assert isinstance(sdk_key, str) and sdk_key, "POST sdk-keys returned no key"

    cache_dir = tmp_path / "cache"
    log = logging.getLogger("megooflags.e2e")
    provider = MegooFlagsProvider(
        sdk_key=sdk_key, base_url=BASE, cache_dir=cache_dir, flush_interval=10.0, logger=log
    )
    api.set_provider_and_wait(provider, project)
    client = api.get_client(project)
    changes: list[EventDetails] = []
    client.add_handler(ProviderEvent.PROVIDER_CONFIGURATION_CHANGED, changes.append)
    ctx = EvaluationContext("user-1")

    # Check 1: the downloaded ruleset is evaluated.
    assert client.get_boolean_value("e2e-flag", False, ctx) is True

    # Check 2: a toggle arrives over SSE within 5 s.
    must(200, "POST", f"{flag_path}/toggle", {"enabled": False})
    until(
        "e2e-flag false after the toggle",
        lambda: client.get_boolean_value("e2e-flag", False, ctx) is False,
        5.0,
    )
    until("a configuration-changed event", lambda: len(changes) > 0, 5.0)

    # Check 3: evaluation counts are sent at shutdown and accepted.
    for _ in range(20):
        client.get_boolean_value("e2e-flag", False, ctx)
    provider.shutdown()

    def counted() -> bool:
        total = must(200, "GET", f"{flag_path}/evaluations?range=24h").get("total")
        return isinstance(total, int) and total >= 21

    until("evaluations total >= 21", counted, 10.0, 0.5)

    # Check 4: with the API unreachable, a new provider serves the cache.
    offline = MegooFlagsProvider(
        sdk_key=sdk_key,
        base_url=f"http://127.0.0.1:{closed_port()}",
        cache_dir=cache_dir,
        init_timeout=1.0,
        logger=log,
    )
    api.set_provider_and_wait(offline, f"{project}-offline")
    assert api.get_client(f"{project}-offline").get_boolean_value("e2e-flag", True, ctx) is False

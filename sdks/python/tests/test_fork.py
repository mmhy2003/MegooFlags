"""A forked child (gunicorn, uWSGI) restarts the provider's threads with empty event buffers."""

from __future__ import annotations

import json
import os
import threading
import traceback
from collections.abc import Iterator
from typing import Any

import pytest
from fake_server import FakeServer, Scripted, Stream
from openfeature import api
from openfeature.evaluation_context import EvaluationContext
from ruleset_gen import provider_ruleset

from megooflags import MegooFlagsProvider

KEY = "mf_server_fork-secret-value"
STREAM = "/sdk/v1/stream"
EVENTS = "/sdk/v1/events"

pytestmark = [
    pytest.mark.skipif(not hasattr(os, "fork"), reason="os.fork is not available"),
    # Python warns about forking a process that runs threads; that is the case under test.
    pytest.mark.filterwarnings(
        "ignore:.*use of fork\\(\\) may lead to deadlocks:DeprecationWarning"
    ),
]


@pytest.fixture
def server() -> Iterator[FakeServer]:
    with FakeServer() as s:
        s.reply(STREAM, Stream())
        s.reply(EVENTS, Scripted(202))
        yield s
        api.shutdown()


def in_child(check: Any) -> dict[str, Any]:
    """Fork, run check() in the child and return what it reported through a pipe. The child
    ends with os._exit, so it never runs the parent's pytest teardown."""
    r, w = os.pipe()
    pid = os.fork()
    if pid == 0:  # the child
        os.close(r)
        code = 0
        try:
            report = {"ok": True, **check()}
        except BaseException:
            report = {"ok": False, "error": traceback.format_exc()}
            code = 1
        with os.fdopen(w, "w") as f:
            json.dump(report, f)
        os._exit(code)
    os.close(w)
    with os.fdopen(r) as f:
        data = f.read()
    _, status = os.waitpid(pid, 0)
    report: dict[str, Any] = json.loads(data)
    assert report["ok"], report.get("error")
    assert os.waitstatus_to_exitcode(status) == 0
    return report


def sdk_threads() -> list[str]:
    return sorted(t.name for t in threading.enumerate() if t.name.startswith("megooflags-"))


def exposure_keys(server: FakeServer) -> list[str]:
    keys: list[str] = []
    for req in server.received(EVENTS):
        keys += [e["context"]["key"] for e in json.loads(req.body)["exposures"]]
    return sorted(keys)


def test_a_forked_child_restarts_its_threads_with_empty_buffers(server: FakeServer) -> None:
    p = MegooFlagsProvider(sdk_key=KEY, base_url=server.url, send_context=True)
    domain = "fork"
    t = threading.Thread(target=lambda: api.set_provider_and_wait(p, domain))
    t.start()
    server.next_stream().write(f"event: put\nid: e1\ndata: {provider_ruleset()}\n\n")
    t.join(5)
    client = api.get_client(domain)
    assert client.get_string_value("string-flag", "x", EvaluationContext("parent")) == "blue"
    assert sdk_threads() == ["megooflags-events", "megooflags-stream"]

    def check() -> dict[str, Any]:
        threads = sdk_threads()
        value = p.resolve_string_details("string-flag", "x", EvaluationContext("child")).value
        p.shutdown()
        return {"threads": threads, "value": value, "after": sdk_threads()}

    report = in_child(check)
    assert report["threads"] == ["megooflags-events", "megooflags-stream"]
    assert report["value"] == "blue"
    assert report["after"] == []
    # The child sent only its own evaluation, not the one the parent had recorded.
    assert exposure_keys(server) == ["child"]

    # The parent keeps working and sends its own evaluation on shutdown.
    assert sdk_threads() == ["megooflags-events", "megooflags-stream"]
    assert client.get_string_value("string-flag", "x", EvaluationContext("parent")) == "blue"
    api.shutdown()
    assert exposure_keys(server) == ["child", "parent"]
    assert sdk_threads() == []


def test_a_shut_down_provider_stays_down_in_the_child(server: FakeServer) -> None:
    p = MegooFlagsProvider(sdk_key=KEY, base_url=server.url)
    t = threading.Thread(target=lambda: api.set_provider_and_wait(p, "fork-down"))
    t.start()
    server.next_stream().write(f"event: put\nid: e1\ndata: {provider_ruleset()}\n\n")
    t.join(5)
    p.shutdown()

    report = in_child(lambda: {"threads": sdk_threads()})
    assert report["threads"] == []
    assert len(server.received(STREAM)) == 1

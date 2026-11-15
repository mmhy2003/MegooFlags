"""The provider's start-up, provider events and shutdown, through the real OpenFeature API and
the fake server."""

from __future__ import annotations

import itertools
import json
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from fake_server import FakeServer, Scripted, Stream
from openfeature import api
from openfeature.client import OpenFeatureClient
from openfeature.evaluation_context import EvaluationContext
from openfeature.event import EventDetails, ProviderEvent
from openfeature.exception import ErrorCode, GeneralError, ProviderFatalError
from ruleset_gen import changed_provider_ruleset, provider_ruleset
from waiting import wait_for

import megooflags.provider
from megooflags import MegooFlagsProvider
from megooflags._delivery.cache import Cache
from megooflags._delivery.sync import Tuning

KEY = "mf_server_lifecycle-secret-value"
STREAM = "/sdk/v1/stream"
RULESET = "/sdk/v1/ruleset"
EVENTS = "/sdk/v1/events"

_domains = itertools.count(1)


def put(event_id: str, data: str) -> str:
    return f"event: put\nid: {event_id}\ndata: {data}\n\n"


@pytest.fixture(autouse=True)
def _tuning(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        megooflags.provider,
        "_sync_tuning",
        Tuning(retry_base=0.02, retry_max=0.2, stable_after=0.1, dead_timeout=5.0),
    )


@pytest.fixture
def server() -> Iterator[FakeServer]:
    with FakeServer() as s:
        s.reply(STREAM, Stream())
        s.reply(EVENTS, Scripted(202))
        yield s
        api.shutdown()
        assert [t.name for t in threading.enumerate() if t.name.startswith("megooflags-")] == []


@dataclass
class Registration:
    """A provider registered under its own domain, with the events of that domain."""

    domain: str
    client: OpenFeatureClient
    provider: MegooFlagsProvider
    events: list[tuple[ProviderEvent, EventDetails]] = field(default_factory=list)
    error: BaseException | None = None
    _done: threading.Event = field(default_factory=threading.Event)

    def ready(self, timeout: float = 5.0) -> BaseException | None:
        """Wait for set_provider_and_wait to return; its exception, or None."""
        assert self._done.wait(timeout), "initialisation did not return"
        return self.error

    def settled(self, within: float) -> bool:
        return self._done.wait(within)

    def types(self) -> list[ProviderEvent]:
        return [t for t, _ in list(self.events)]

    def next(self, kind: ProviderEvent, n: int = 1) -> EventDetails:
        """Wait for the n-th (1-based) event of kind."""
        wait_for(lambda: len([t for t in self.types() if t == kind]) >= n)
        return [d for t, d in list(self.events) if t == kind][n - 1]


def register(p: MegooFlagsProvider) -> Registration:
    """Register p under a new domain on a background thread, like set_provider_and_wait, and
    record the domain's events."""
    domain = f"lifecycle-{next(_domains)}"
    r = Registration(domain, api.get_client(domain), p)

    def recorder(kind: ProviderEvent) -> Any:
        def record(details: EventDetails) -> None:
            if details.provider_name == "MegooFlags":
                r.events.append((kind, details))

        return record

    for kind in ProviderEvent:
        r.client.add_handler(kind, recorder(kind))

    def init() -> None:
        try:
            api.set_provider_and_wait(p, domain)
        except BaseException as err:
            r.error = err
        finally:
            r._done.set()

    threading.Thread(target=init, name="test-init", daemon=True).start()
    return r


def provider(server: FakeServer, **opts: Any) -> MegooFlagsProvider:
    args: dict[str, Any] = {"sdk_key": KEY, "base_url": server.url}
    args.update(opts)
    return MegooFlagsProvider(**args)


# initialize


def test_waits_for_the_first_put(server: FakeServer) -> None:
    r = register(provider(server))
    conn = server.next_stream()
    assert not r.settled(0.1)
    conn.write(put('"e1"', provider_ruleset()))
    assert r.ready() is None
    assert r.client.get_string_value("string-flag", "x") == "blue"
    r.next(ProviderEvent.PROVIDER_READY)
    time.sleep(0.05)
    assert r.types() == [ProviderEvent.PROVIDER_READY]


def test_times_out_keeps_retrying_and_emits_ready_with_the_ruleset(server: FakeServer) -> None:
    r = register(provider(server, init_timeout=0.2))
    conn = server.next_stream()
    err = r.ready()
    assert isinstance(err, GeneralError)
    assert not isinstance(err, ProviderFatalError)
    assert "no ruleset within 0.2s" in str(err.error_message)
    d = r.client.get_string_details("string-flag", "x")
    assert (d.value, d.error_code) == ("x", ErrorCode.PROVIDER_NOT_READY)

    conn.write(put('"e1"', provider_ruleset()))
    r.next(ProviderEvent.PROVIDER_READY)
    assert r.client.get_string_value("string-flag", "x") == "blue"
    assert r.types() == [ProviderEvent.PROVIDER_ERROR, ProviderEvent.PROVIDER_READY]


def test_is_ready_from_the_bootstrap_file_then_refreshed_by_the_stream(
    server: FakeServer, tmp_path: Path
) -> None:
    boot = tmp_path / "ruleset.json"
    boot.write_text(provider_ruleset())
    r = register(provider(server, bootstrap_file=boot, cache_dir=tmp_path / "cache"))
    assert r.ready() is None
    assert r.client.get_string_value("string-flag", "x") == "blue"

    conn = server.next_stream()
    assert "last-event-id" not in conn.request.headers
    conn.write(put('"e2"', changed_provider_ruleset()))
    changed = r.next(ProviderEvent.PROVIDER_CONFIGURATION_CHANGED)
    assert changed.flags_changed == ["new-flag", "string-flag", "targeted"]
    assert r.client.get_string_value("string-flag", "x") == "red"
    cached = Cache(tmp_path / "cache", KEY).load()
    assert cached is not None and cached.etag == '"e2"'


def test_is_ready_from_the_cache_offline_without_any_request(
    server: FakeServer, tmp_path: Path
) -> None:
    Cache(tmp_path, KEY).save(provider_ruleset(), '"c1"')
    r = register(provider(server, offline=True, cache_dir=tmp_path))
    assert r.ready() is None
    assert r.client.get_string_value("string-flag", "x") == "blue"
    r.provider.shutdown()
    assert server.requests == []


def test_sends_the_cached_etag_as_last_event_id(server: FakeServer, tmp_path: Path) -> None:
    Cache(tmp_path, KEY).save(provider_ruleset(), '"c1"')
    r = register(provider(server, cache_dir=tmp_path))
    assert r.ready() is None
    assert server.next_stream().request.headers["last-event-id"] == '"c1"'


def test_prefers_the_bootstrap_file_to_the_cache(server: FakeServer, tmp_path: Path) -> None:
    Cache(tmp_path, KEY).save(changed_provider_ruleset(), '"c1"')
    boot = tmp_path / "ruleset.json"
    boot.write_text(provider_ruleset())
    r = register(provider(server, offline=True, bootstrap_file=boot, cache_dir=tmp_path))
    assert r.ready() is None
    assert r.client.get_string_value("string-flag", "x") == "blue"


def test_falls_back_to_the_cache_after_a_bad_bootstrap_file(
    server: FakeServer, tmp_path: Path
) -> None:
    Cache(tmp_path, KEY).save(changed_provider_ruleset(), '"c1"')
    boot = tmp_path / "ruleset.json"
    boot.write_text("{")
    r = register(provider(server, offline=True, bootstrap_file=boot, cache_dir=tmp_path))
    assert r.ready() is None
    assert r.client.get_string_value("string-flag", "x") == "red"


def test_warns_about_a_corrupt_cache(
    server: FakeServer, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    Cache(tmp_path, KEY).save(provider_ruleset(), '"c1"')
    (cache_file,) = tmp_path.glob("megooflags-*.json")
    cache_file.write_text("{")
    r = register(provider(server, offline=True, cache_dir=tmp_path))
    assert isinstance(r.ready(), GeneralError)
    assert "could not read the ruleset cache" in caplog.text


def test_fails_offline_without_a_ruleset(server: FakeServer, tmp_path: Path) -> None:
    r = register(provider(server, offline=True, cache_dir=tmp_path))
    err = r.ready()
    assert isinstance(err, GeneralError)
    assert not isinstance(err, ProviderFatalError)
    assert "offline" in str(err.error_message)
    assert server.requests == []


def test_is_fatal_for_a_client_key(server: FakeServer) -> None:
    r = register(provider(server, sdk_key="mf_client_abc"))
    assert isinstance(r.ready(), ProviderFatalError)
    d = r.client.get_string_details("string-flag", "x")
    assert (d.value, d.error_code) == ("x", ErrorCode.PROVIDER_FATAL)
    assert server.requests == []


@pytest.mark.parametrize("status", [401, 403])
def test_is_fatal_when_the_server_refuses_the_key(server: FakeServer, status: int) -> None:
    with FakeServer() as refusing:
        refusing.reply(STREAM, Scripted(status))
        refused(refusing)


def refused(server: FakeServer) -> None:
    r = register(provider(server))
    err = r.ready()
    assert isinstance(err, ProviderFatalError)
    assert KEY not in str(err.error_message)
    d = r.client.get_string_details("string-flag", "x")
    assert (d.value, d.error_code) == ("x", ErrorCode.PROVIDER_FATAL)
    d2 = r.provider.resolve_string_details("string-flag", "x")
    assert d2.error_code == ErrorCode.PROVIDER_FATAL
    time.sleep(0.1)
    assert len(server.received(STREAM)) == 1


def test_fails_after_shutdown(server: FakeServer, tmp_path: Path) -> None:
    boot = tmp_path / "ruleset.json"
    boot.write_text(provider_ruleset())
    p = provider(server, bootstrap_file=boot)
    p.shutdown()
    with pytest.raises(GeneralError, match="shut down") as info:
        p.initialize(EvaluationContext())
    assert not isinstance(info.value, ProviderFatalError)


def test_shutdown_ends_a_waiting_initialize(server: FakeServer) -> None:
    r = register(provider(server, init_timeout=10))
    server.next_stream()
    r.provider.shutdown()
    assert isinstance(r.ready(1.0), GeneralError)


# events


def test_emits_the_fatal_error_on_revoked_and_keeps_serving(server: FakeServer) -> None:
    r = register(provider(server))
    conn = server.next_stream()
    conn.write(put('"e2"', changed_provider_ruleset()))
    assert r.ready() is None
    r.client.get_string_value("string-flag", "x")

    conn.write("event: revoked\ndata: {}\n\n")
    ev = r.next(ProviderEvent.PROVIDER_ERROR)
    assert ev.error_code == ErrorCode.PROVIDER_FATAL
    assert ev.message and KEY not in ev.message
    # The provider keeps serving the last ruleset; the OpenFeature SDK puts a provider that
    # reported PROVIDER_FATAL in the FATAL state and returns the default itself.
    direct = r.provider.resolve_string_details("string-flag", "x")
    assert (direct.value, direct.variant, direct.error_code) == ("red", "red", None)
    d = r.client.get_string_details("string-flag", "x")
    assert (d.value, d.error_code) == ("x", ErrorCode.PROVIDER_FATAL)

    time.sleep(0.2)
    assert len(server.received(STREAM)) == 1
    api.shutdown()
    assert server.received(EVENTS) == []
    assert r.types().count(ProviderEvent.PROVIDER_ERROR) == 1


def test_a_refused_events_post_is_fatal(server: FakeServer) -> None:
    with FakeServer() as refusing:
        refusing.reply(STREAM, Stream())
        refusing.reply(EVENTS, Scripted(401))
        events_refused(refusing)


def events_refused(server: FakeServer) -> None:
    r = register(provider(server))
    conn = server.next_stream()
    conn.write(put('"e1"', provider_ruleset()))
    assert r.ready() is None
    for i in range(1000):
        r.client.get_string_value("string-flag", "x", EvaluationContext(f"user-{i}"))
    ev = r.next(ProviderEvent.PROVIDER_ERROR)
    assert ev.error_code == ErrorCode.PROVIDER_FATAL
    wait_for(lambda: not any(t.name == "megooflags-stream" for t in threading.enumerate()))
    assert len(server.received(EVENTS)) == 1
    assert r.provider.resolve_string_details("string-flag", "x").value == "blue"


def test_emits_stale_on_a_disconnect_and_ready_on_reconnect(server: FakeServer) -> None:
    r = register(provider(server))
    conn = server.next_stream()
    conn.write(put('"e1"', provider_ruleset()))
    assert r.ready() is None

    conn.drop()
    r.next(ProviderEvent.PROVIDER_STALE)
    assert r.client.get_string_value("string-flag", "x") == "blue"
    r.next(ProviderEvent.PROVIDER_READY, 2)
    time.sleep(0.1)
    assert r.types() == [
        ProviderEvent.PROVIDER_READY,
        ProviderEvent.PROVIDER_STALE,
        ProviderEvent.PROVIDER_READY,
    ]


def test_emits_nothing_after_shutdown(server: FakeServer) -> None:
    r = register(provider(server))
    conn = server.next_stream()
    conn.write(put('"e1"', provider_ruleset()))
    assert r.ready() is None
    seen: list[str] = []
    r.provider.attach(lambda _p, kind, _d: seen.append(kind.value))
    r.provider.shutdown()
    conn.write(put('"e2"', changed_provider_ruleset()))
    time.sleep(0.1)
    assert seen == []
    assert r.provider.resolve_string_details("string-flag", "x").value == "blue"


# shutdown


@pytest.mark.parametrize("send_context", [False, True])
def test_shutdown_sends_the_events(server: FakeServer, send_context: bool) -> None:
    r = register(provider(server, send_context=send_context))
    server.next_stream().write(put('"e1"', provider_ruleset()))
    assert r.ready() is None
    alice = EvaluationContext("alice", {"plan": "pro"})
    for _ in range(3):
        r.client.get_string_value("string-flag", "x", alice)
    r.client.get_string_value("string-flag", "x", EvaluationContext("bob"))
    r.client.get_boolean_value("bool-flag", False, alice)
    r.client.get_string_value("missing", "x", alice)
    api.shutdown()

    batches = [json.loads(req.body) for req in server.received(EVENTS)]
    counts: dict[str, int] = {}
    for b in batches:
        for c in b["counts"]:
            k = f"{c['flag_key']}/{c['variant']}"
            counts[k] = counts.get(k, 0) + c["count"]
    exposures = [e for b in batches for e in b["exposures"]]
    assert counts == {"string-flag/blue": 4, "bool-flag/on": 1, "missing/None": 1}
    assert [(e["flag_key"], e["variant"]) for e in exposures] == [
        ("string-flag", "blue"),
        ("string-flag", "blue"),
        ("bool-flag", "on"),
    ]
    if send_context:
        assert exposures[0]["context"] == {"key": "alice", "plan": "pro"}
        assert exposures[1]["context"] == {"key": "bob"}
    else:
        assert all("context" not in e for e in exposures)
    assert server.received(EVENTS)[0].headers["authorization"] == f"Bearer {KEY}"


def test_shutdown_is_idempotent_and_keeps_serving_the_last_ruleset(server: FakeServer) -> None:
    r = register(provider(server))
    server.next_stream().write(put('"e1"', provider_ruleset()))
    assert r.ready() is None
    r.client.get_string_value("string-flag", "x")
    r.provider.shutdown()
    r.provider.shutdown()
    assert len(server.received(EVENTS)) == 1

    d = r.provider.resolve_string_details("string-flag", "x", EvaluationContext("later"))
    assert d.value == "blue"
    r.provider.shutdown()
    time.sleep(0.05)
    assert len(server.received(EVENTS)) == 1


def test_offline_records_nothing(server: FakeServer, tmp_path: Path) -> None:
    Cache(tmp_path, KEY).save(provider_ruleset(), '"c1"')
    r = register(provider(server, offline=True, cache_dir=tmp_path))
    assert r.ready() is None
    r.client.get_string_value("string-flag", "x")
    api.shutdown()
    assert server.requests == []


def test_polls_only_with_disable_stream(server: FakeServer) -> None:
    server.reply(RULESET, Scripted(200, {"ETag": '"p1"'}, provider_ruleset()))
    r = register(provider(server, disable_stream=True))
    assert r.ready() is None
    assert r.client.get_string_value("string-flag", "x") == "blue"
    assert server.received(STREAM) == []

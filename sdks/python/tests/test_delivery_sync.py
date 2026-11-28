import json
import logging
import os
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from fake_server import FakeServer, Scripted, Stream
from ruleset_gen import cl, flag_json, ruleset_json
from waiting import wait_for

from megooflags._delivery.cache import Cache
from megooflags._delivery.store import Snapshot, Store
from megooflags._delivery.sync import Status, Sync, Tuning
from megooflags._transport import FatalError

KEY = "mf_server_sync-key-value"
STREAM = "/sdk/v1/stream"
RULESET = "/sdk/v1/ruleset"
LOGGER = "megooflags.test.sync"


def raw(env: str) -> str:
    return json.dumps({**ruleset_json([]), "environment": env})


def put(event_id: str, data: str) -> str:
    return f"event: put\nid: {event_id}\ndata: {data}\n\n"


@dataclass
class Seen:
    changes: list[tuple[Snapshot | None, Snapshot]] = field(default_factory=list)
    statuses: list[tuple[Status, Exception | None]] = field(default_factory=list)

    def status_names(self) -> list[Status]:
        return [s for s, _ in self.statuses]


@dataclass
class Env:
    server: FakeServer
    dir: Path
    store: Store
    cache: Cache
    seen: Seen
    new: Callable[..., Sync]


@pytest.fixture
def env(no_thread_leaks: None, tmp_path: Path) -> Iterator[Env]:
    syncs: list[Sync] = []
    store = Store()
    cache = Cache(tmp_path, KEY)
    seen = Seen()
    with FakeServer() as server:

        def new(**opts: Any) -> Sync:
            args: dict[str, Any] = {
                "base_url": server.url,
                "sdk_key": KEY,
                "logger": logging.getLogger(LOGGER),
                "store": store,
                "cache": cache,
                "on_change": lambda old, new: seen.changes.append((old, new)),
                "on_status": lambda st, err: seen.statuses.append((st, err)),
                "tuning": Tuning(retry_base=0.01, retry_max=0.05, poll_interval=0.02),
            }
            args.update(opts)
            s = Sync(**args)
            syncs.append(s)
            return s

        yield Env(server, tmp_path, store, cache, seen, new)
        for s in syncs:
            s.stop()


# apply


def test_a_bootstrap_apply_does_not_write_the_cache(env: Env) -> None:
    err = env.new().apply(raw("boot"), "", "bootstrap")
    assert err is None
    snap = env.store.load()
    assert snap is not None
    assert snap.source == "bootstrap"
    assert snap.ruleset.environment == "boot"
    assert os.listdir(env.dir) == []
    assert len(env.seen.changes) == 1
    assert env.seen.changes[0][0] is None


def test_an_invalid_ruleset_keeps_the_previous_one(
    env: Env, caplog: pytest.LogCaptureFixture
) -> None:
    s = env.new()
    s.apply(raw("one"), '"1"', "poll")
    err = s.apply('{"schema_version":99}', '"2"', "poll")
    assert isinstance(err, ValueError)
    snap = env.store.load()
    assert snap is not None and snap.etag == '"1"'
    assert len(env.seen.changes) == 1
    assert "invalid ruleset" in caplog.text


def test_each_ruleset_warning_is_logged_once(env: Env, caplog: pytest.LogCaptureFixture) -> None:
    rule = {
        "id": "r1",
        "clauses": [cl("email", "matches", False, "(")],
        "serve": {"variation": "on"},
    }
    bad = json.dumps(ruleset_json([flag_json("re", rules=[rule])]))
    s = env.new()
    s.apply(bad, '"1"', "stream")
    s.apply(bad, '"2"', "stream")
    regex = [r.getMessage() for r in caplog.records if "regex" in r.getMessage()]
    assert len(regex) == 1
    assert "flags.re" in regex[0]


# Stream and poll loops


def test_a_stream_put_updates_the_store_and_the_cache(env: Env) -> None:
    env.server.reply(STREAM, Stream())
    env.new().start()
    conn = env.server.next_stream()
    conn.write(put('"e1"', raw("streamed")))
    wait_for(lambda: len(env.seen.changes) == 1)
    snap = env.store.load()
    assert snap is not None
    assert (snap.etag, snap.source) == ('"e1"', "stream")
    cached = env.cache.load()
    assert cached is not None
    assert cached.etag == '"e1"'
    assert cached.ruleset == json.loads(raw("streamed"))
    assert env.seen.status_names() == ["ready"]


def test_the_stores_etag_is_sent_as_last_event_id(env: Env) -> None:
    env.server.reply(STREAM, Stream())
    s = env.new()
    s.apply(raw("boot"), '"boot-etag"', "bootstrap")
    s.start()
    conn = env.server.next_stream()
    assert conn.request.headers["last-event-id"] == '"boot-etag"'


def test_an_invalid_put_keeps_the_previous_ruleset_and_the_stream(env: Env) -> None:
    env.server.reply(STREAM, Stream())
    env.new().start()
    conn = env.server.next_stream()
    conn.write(put('"1"', raw("one")))
    conn.write(put('"2"', "{nope"))
    conn.write(put('"3"', raw("three")))
    wait_for(lambda: len(env.seen.changes) == 2)
    snap = env.store.load()
    assert snap is not None and snap.etag == '"3"'
    assert len(env.server.received(STREAM)) == 1


def test_polls_after_two_stream_failures_and_stops_polling_on_reconnect(env: Env) -> None:
    env.server.reply(STREAM, Scripted(500), Scripted(500), Stream())
    env.server.reply(RULESET, Scripted(200, {"ETag": '"p1"'}, raw("polled")))
    env.new(tuning=Tuning(retry_base=0.06, retry_max=0.06, poll_interval=0.01)).start()
    env.server.next_stream()
    wait_for(lambda: "ready" in env.seen.status_names())
    polls = env.server.received(RULESET)
    assert len(polls) >= 1
    # The first poll came after the second stream failure.
    streams = env.server.received(STREAM)
    assert polls[0].at >= streams[1].at
    snap = env.store.load()
    assert snap is not None and snap.source == "poll"
    cached = env.cache.load()
    assert cached is not None and cached.etag == '"p1"'
    assert env.seen.status_names() == ["stale", "stale", "ready"]
    count = len(env.server.received(RULESET))
    time.sleep(0.1)
    assert len(env.server.received(RULESET)) == count


def test_a_single_stream_failure_does_not_poll(env: Env) -> None:
    env.server.reply(STREAM, Scripted(500), Stream())
    env.server.reply(RULESET, Scripted(200, body=raw("polled")))
    env.new().start()
    env.server.next_stream()
    time.sleep(0.05)
    assert env.server.received(RULESET) == []


def test_polls_are_at_least_the_poll_interval_apart(env: Env) -> None:
    env.server.reply(RULESET, Scripted(304))
    env.new(disable_stream=True, tuning=Tuning(poll_interval=0.1)).start()
    time.sleep(0.35)
    at = [r.at for r in env.server.received(RULESET)]
    assert 3 <= len(at) <= 4
    for a, b in zip(at, at[1:], strict=False):
        assert b - a >= 0.095


def test_a_poll_429_waits_its_retry_after(env: Env) -> None:
    env.server.reply(RULESET, Scripted(429, {"Retry-After": "1"}), Scripted(304))
    env.new(disable_stream=True).start()
    wait_for(lambda: len(env.server.received(RULESET)) == 2, timeout=3)
    first, second = env.server.received(RULESET)
    assert second.at - first.at >= 0.95


def test_disable_stream_polls_only_and_reports_stale_then_ready(env: Env) -> None:
    env.server.reply(
        RULESET,
        Scripted(500),
        Scripted(200, {"ETag": '"v1"'}, raw("polled")),
        Scripted(304),
    )
    env.new(disable_stream=True).start()
    wait_for(lambda: env.seen.status_names()[:3] == ["stale", "ready", "ready"])
    assert env.server.received(STREAM) == []
    snap = env.store.load()
    assert snap is not None and snap.etag == '"v1"'


def test_revoked_is_fatal_once_and_stops(env: Env) -> None:
    env.server.reply(STREAM, Stream())
    env.server.reply(RULESET, Scripted(200, body=raw("polled")))
    env.new().start()
    conn = env.server.next_stream()
    conn.write("event: revoked\ndata: {}\n\n")
    wait_for(lambda: "fatal" in env.seen.status_names())
    time.sleep(0.08)
    assert env.seen.status_names() == ["ready", "fatal"]
    fatal = env.seen.statuses[1][1]
    assert isinstance(fatal, FatalError)
    assert fatal.reason == "revoked"
    assert len(env.server.received(STREAM)) == 1
    assert env.server.received(RULESET) == []


def test_a_403_on_a_poll_is_fatal_and_stops_polling(env: Env) -> None:
    env.server.reply(RULESET, Scripted(403))
    env.new(disable_stream=True).start()
    wait_for(lambda: "fatal" in env.seen.status_names())
    time.sleep(0.08)
    assert env.seen.status_names() == ["fatal"]
    err = env.seen.statuses[0][1]
    assert isinstance(err, FatalError) and err.status == 403
    assert len(env.server.received(RULESET)) == 1


def test_a_fatal_poll_stops_the_stream(env: Env) -> None:
    env.server.reply(STREAM, Scripted(500))
    env.server.reply(RULESET, Scripted(401))
    env.new(tuning=Tuning(retry_base=0.03, retry_max=0.03, poll_interval=0.01)).start()
    wait_for(lambda: "fatal" in env.seen.status_names())
    streams = len(env.server.received(STREAM))
    time.sleep(0.1)
    assert len(env.server.received(STREAM)) == streams
    assert env.seen.status_names().count("fatal") == 1


def test_stop_is_safe_before_start_and_idempotent(env: Env) -> None:
    s = env.new()
    s.stop()
    s.stop()
    s.start()
    assert env.server.requests == []


def test_stop_ends_the_loops_and_silences_callbacks(env: Env) -> None:
    env.server.reply(STREAM, Stream())
    s = env.new()
    s.start()
    conn = env.server.next_stream()
    started = time.monotonic()
    stoppers = [threading.Thread(target=s.stop) for _ in range(2)]
    for t in stoppers:
        t.start()
    for t in stoppers:
        t.join(5)
    assert time.monotonic() - started < 0.5
    before = len(env.seen.statuses)
    conn.write(put('"late"', raw("late")))
    s.start()
    time.sleep(0.05)
    assert len(env.seen.statuses) == before
    assert env.seen.changes == []
    assert len(env.server.received(STREAM)) == 1


def test_stop_ends_a_poll_in_flight(env: Env) -> None:
    env.server.reply(RULESET, Stream(headers={"Content-Length": "10"}))
    s = env.new(disable_stream=True)
    s.start()
    env.server.next_stream()
    started = time.monotonic()
    s.stop()
    assert time.monotonic() - started < 0.5
    assert env.seen.statuses == []

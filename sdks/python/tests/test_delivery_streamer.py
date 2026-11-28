import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import Any

import pytest
from fake_server import FakeServer, Scripted, Stream
from waiting import wait_for

from megooflags._delivery.streamer import DeadConnectionError, Streamer
from megooflags._transport import (
    USER_AGENT,
    Backoff,
    Cancel,
    FatalError,
    RetryAfterError,
    sdk_url,
)

KEY = "mf_server_streamer-key-value"
STREAM = "/sdk/v1/stream"


@dataclass
class Seen:
    puts: list[tuple[str, str]] = field(default_factory=list)
    connected: int = 0
    disconnects: list[tuple[Exception, int]] = field(default_factory=list)


class Running:
    """A Streamer running on a test thread until it returns."""

    def __init__(self, streamer: Streamer) -> None:
        self.cancel = Cancel()
        self.result: FatalError | None = None
        self._streamer = streamer
        self._thread = threading.Thread(target=self._run, name="streamer-test", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        self.result = self._streamer.run(self.cancel)

    def stop(self) -> None:
        self.cancel.cancel()
        self.join()

    def join(self, timeout: float = 5.0) -> FatalError | None:
        self._thread.join(timeout)
        assert not self._thread.is_alive()
        return self.result


@pytest.fixture
def server(no_thread_leaks: None) -> Iterator[FakeServer]:
    with FakeServer() as s:
        yield s


@pytest.fixture
def start(server: FakeServer) -> Iterator[Callable[..., tuple[Seen, Running]]]:
    running: list[Running] = []

    def start_streamer(**opts: Any) -> tuple[Seen, Running]:
        seen = Seen()

        def on_put(raw: str, event_id: str) -> bool:
            seen.puts.append((raw, event_id))
            return raw != "invalid"

        def on_connected() -> None:
            seen.connected += 1

        args: dict[str, Any] = {
            "url": sdk_url(server.url, "stream"),
            "sdk_key": KEY,
            "on_put": on_put,
            "on_connected": on_connected,
            "on_disconnected": lambda err, n: seen.disconnects.append((err, n)),
            "backoff": Backoff(0.01, 0.1, lambda: 1.0),
        }
        args.update(opts)
        r = Running(Streamer(**args))
        running.append(r)
        return seen, r

    yield start_streamer
    for r in running:
        r.stop()


def test_put_events_go_to_on_put_with_the_stream_headers(
    server: FakeServer, start: Callable[..., tuple[Seen, Running]]
) -> None:
    server.reply(STREAM, Stream())
    seen, _ = start()
    conn = server.next_stream()
    conn.write('event: put\nid: "e1"\ndata: {"a":1}\n\n')
    wait_for(lambda: len(seen.puts) == 1)
    assert seen.puts == [('{"a":1}', '"e1"')]
    assert seen.connected == 1
    req = conn.request
    assert req.method == "GET"
    assert req.headers["authorization"] == f"Bearer {KEY}"
    assert req.headers["accept"] == "text/event-stream"
    assert req.headers["cache-control"] == "no-cache"
    assert req.headers["user-agent"] == USER_AGENT
    assert "last-event-id" not in req.headers
    assert req.url == STREAM
    assert KEY not in req.url


def test_sends_last_event_id_when_there_is_an_etag(
    server: FakeServer, start: Callable[..., tuple[Seen, Running]]
) -> None:
    server.reply(STREAM, Stream())
    start(last_event_id=lambda: '"etag-7"')
    conn = server.next_stream()
    assert conn.request.headers["last-event-id"] == '"etag-7"'


def test_pings_keep_the_stream_alive(
    server: FakeServer, start: Callable[..., tuple[Seen, Running]]
) -> None:
    server.reply(STREAM, Stream())
    seen, _ = start(dead_timeout=0.15)
    conn = server.next_stream()
    for _ in range(8):
        time.sleep(0.05)
        conn.write(": ping\n")
    assert len(server.received(STREAM)) == 1
    assert seen.disconnects == []


def test_a_silent_connection_is_dead_and_reconnects(
    server: FakeServer, start: Callable[..., tuple[Seen, Running]]
) -> None:
    server.reply(STREAM, Stream())
    seen, _ = start(dead_timeout=0.05)
    server.next_stream()
    server.next_stream()
    err, failures = seen.disconnects[0]
    assert failures == 1
    assert isinstance(err, DeadConnectionError)
    assert "dead" in str(err)


def test_unknown_events_are_ignored_and_an_invalid_put_does_not_end_the_stream(
    server: FakeServer, start: Callable[..., tuple[Seen, Running]]
) -> None:
    server.reply(STREAM, Stream())
    seen, _ = start()
    conn = server.next_stream()
    conn.write("event: mystery\ndata: x\n\n")
    conn.write("data: unnamed\n\n")
    conn.write("event: put\nid: 1\ndata: invalid\n\n")
    conn.write("event: put\nid: 2\ndata: {}\n\n")
    wait_for(lambda: len(seen.puts) == 2)
    assert [p[1] for p in seen.puts] == ["1", "2"]
    assert seen.disconnects == []
    assert len(server.received(STREAM)) == 1


def test_revoked_stops_with_a_fatal_error(
    server: FakeServer, start: Callable[..., tuple[Seen, Running]]
) -> None:
    server.reply(STREAM, Stream())
    seen, running = start()
    conn = server.next_stream()
    conn.write("event: revoked\ndata: {}\n\n")
    err = running.join()
    assert isinstance(err, FatalError)
    assert err.reason == "revoked"
    assert seen.disconnects == []
    conn.end()
    assert conn.closed.wait(5)
    assert len(server.received(STREAM)) == 1


@pytest.mark.parametrize("status", [401, 403])
def test_a_refused_key_stops_with_a_fatal_error(
    server: FakeServer, start: Callable[..., tuple[Seen, Running]], status: int
) -> None:
    server.reply(STREAM, Scripted(status))
    seen, running = start()
    err = running.join()
    assert isinstance(err, FatalError)
    assert err.status == status
    assert seen.connected == 0
    assert seen.disconnects == []
    assert len(server.received(STREAM)) == 1


def test_a_429_waits_its_retry_after(
    server: FakeServer, start: Callable[..., tuple[Seen, Running]]
) -> None:
    server.reply(STREAM, Scripted(429, {"Retry-After": "1"}), Stream())
    seen, _ = start()
    server.next_stream()
    first, second = server.received(STREAM)
    assert second.at - first.at >= 0.95
    assert isinstance(seen.disconnects[0][0], RetryAfterError)


def test_5xx_backs_off_with_growing_waits(
    server: FakeServer, start: Callable[..., tuple[Seen, Running]]
) -> None:
    server.reply(STREAM, Scripted(500), Scripted(502), Scripted(503), Scripted(500), Stream())
    seen, _ = start(backoff=Backoff(0.04, 1.0, lambda: 1.0))
    server.next_stream()
    at = [r.at for r in server.received(STREAM)]
    gaps = [b - a for a, b in zip(at, at[1:], strict=False)]
    assert len(gaps) == 4
    for gap, want in zip(gaps, [0.04, 0.08, 0.16, 0.32], strict=True):
        assert gap >= want - 0.005
    assert [n for _, n in seen.disconnects] == [1, 2, 3, 4]


def test_the_retry_field_replaces_the_backoff_base(
    server: FakeServer, start: Callable[..., tuple[Seen, Running]]
) -> None:
    server.reply(STREAM, Stream())
    seen, _ = start(backoff=Backoff(5.0, 60.0, lambda: 1.0))
    conn = server.next_stream()
    conn.write("retry: 20\n\n")
    wait_for(lambda: seen.connected == 1)
    conn.end()
    started = time.monotonic()
    server.next_stream()
    assert time.monotonic() - started < 1


def test_a_stable_connection_resets_the_backoff(
    server: FakeServer, start: Callable[..., tuple[Seen, Running]]
) -> None:
    server.reply(STREAM, Scripted(500), Scripted(500), Stream(), Scripted(500), Stream())
    seen, _ = start(stable_after=0.05)
    conn = server.next_stream()
    time.sleep(0.08)
    conn.end()
    server.next_stream()
    assert [n for _, n in seen.disconnects] == [1, 2, 1, 2]


def test_stop_returns_promptly_from_an_open_stream(
    server: FakeServer, start: Callable[..., tuple[Seen, Running]]
) -> None:
    server.reply(STREAM, Stream())
    seen, running = start()
    conn = server.next_stream()
    started = time.monotonic()
    running.cancel.cancel()
    assert running.join() is None
    assert time.monotonic() - started < 0.5
    conn.end()
    assert conn.closed.wait(5)
    assert seen.disconnects == []


def test_stop_returns_at_once_while_waiting_to_reconnect(
    server: FakeServer, start: Callable[..., tuple[Seen, Running]]
) -> None:
    server.reply(STREAM, Scripted(500))
    seen, running = start(backoff=Backoff(60.0, 60.0))
    wait_for(lambda: len(seen.disconnects) == 1)
    started = time.monotonic()
    running.cancel.cancel()
    assert running.join() is None
    assert time.monotonic() - started < 0.5

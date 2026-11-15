import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass, field

import pytest
from fake_server import FakeServer, Scripted, Stream

from megooflags._delivery.poller import Poller
from megooflags._transport import (
    USER_AGENT,
    Cancel,
    FatalError,
    HttpError,
    RetryAfterError,
    sdk_url,
)

KEY = "mf_server_poller-key-value"
RULESET = "/sdk/v1/ruleset"


@dataclass
class State:
    etag: str = ""
    got: list[tuple[bytes, str]] = field(default_factory=list)


@pytest.fixture
def server(no_thread_leaks: None) -> Iterator[FakeServer]:
    with FakeServer() as s:
        yield s


@pytest.fixture
def state() -> State:
    return State()


def poller(server: FakeServer, state: State, timeout: float = 10.0) -> Poller:
    def on_ruleset(raw: bytes, etag: str) -> bool:
        state.got.append((raw, etag))
        if raw == b"invalid":
            return False
        state.etag = etag
        return True

    return Poller(
        url=sdk_url(server.url, "ruleset"),
        sdk_key=KEY,
        etag=lambda: state.etag,
        on_ruleset=on_ruleset,
        timeout=timeout,
    )


def test_polls_without_if_none_match_first_then_with_the_etag(
    server: FakeServer, state: State
) -> None:
    server.reply(RULESET, Scripted(200, {"ETag": '"v1"'}, b"{}"), Scripted(304))
    p = poller(server, state)
    p.poll(Cancel())
    p.poll(Cancel())
    first, second = server.received(RULESET)
    assert "if-none-match" not in first.headers
    assert first.headers["authorization"] == f"Bearer {KEY}"
    assert first.headers["accept"] == "application/json"
    assert first.headers["user-agent"] == USER_AGENT
    assert KEY not in first.url
    assert second.headers["if-none-match"] == '"v1"'


def test_a_200_body_and_its_etag_go_to_on_ruleset(server: FakeServer, state: State) -> None:
    server.reply(RULESET, Scripted(200, {"ETag": '"v2"'}, b'{"flags":{}}'))
    poller(server, state).poll(Cancel())
    assert state.got == [(b'{"flags":{}}', '"v2"')]


def test_a_200_without_etag_gives_an_empty_one(server: FakeServer, state: State) -> None:
    server.reply(RULESET, Scripted(200, body=b"{}"))
    poller(server, state).poll(Cancel())
    assert state.got == [(b"{}", "")]


def test_304_is_success_without_on_ruleset(server: FakeServer, state: State) -> None:
    state.etag = '"v1"'
    server.reply(RULESET, Scripted(304))
    poller(server, state).poll(Cancel())
    assert state.got == []


def test_an_invalid_ruleset_fails(server: FakeServer, state: State) -> None:
    server.reply(RULESET, Scripted(200, body=b"invalid"))
    with pytest.raises(ValueError, match="invalid ruleset"):
        poller(server, state).poll(Cancel())


@pytest.mark.parametrize("status", [401, 403])
def test_a_refused_key_is_fatal(server: FakeServer, state: State, status: int) -> None:
    server.reply(RULESET, Scripted(status))
    with pytest.raises(FatalError) as info:
        poller(server, state).poll(Cancel())
    assert info.value.status == status


def test_a_429_reports_its_retry_after(server: FakeServer, state: State) -> None:
    server.reply(RULESET, Scripted(429, {"Retry-After": "7"}))
    with pytest.raises(RetryAfterError) as info:
        poller(server, state).poll(Cancel())
    assert info.value.after == 7.0


def test_a_500_is_an_http_error(server: FakeServer, state: State) -> None:
    server.reply(RULESET, Scripted(500, body=b"oops"))
    with pytest.raises(HttpError) as info:
        poller(server, state).poll(Cancel())
    assert info.value.status == 500


def test_times_out(server: FakeServer, state: State) -> None:
    # A held-open response never sends its body.
    server.reply(RULESET, Stream(headers={"Content-Length": "10"}))
    started = time.monotonic()
    with pytest.raises(TimeoutError, match="timed out"):
        poller(server, state, timeout=0.1).poll(Cancel())
    assert time.monotonic() - started < 2
    assert state.got == []


def test_cancel_ends_a_poll_in_flight(server: FakeServer, state: State) -> None:
    server.reply(RULESET, Stream(headers={"Content-Length": "10"}))
    cancel = Cancel()
    errors: list[BaseException] = []

    def run() -> None:
        try:
            poller(server, state).poll(cancel)
        except BaseException as err:
            errors.append(err)

    t = threading.Thread(target=run)
    t.start()
    server.next_stream()
    started = time.monotonic()
    cancel.cancel()
    t.join(5)
    assert not t.is_alive()
    assert time.monotonic() - started < 1
    assert len(errors) == 1
    assert state.got == []

import http.client
import socket
import threading
import time
from collections.abc import Iterator
from email.utils import formatdate

import pytest
from fake_server import FakeServer, Received, Scripted, Stream

from megooflags import __version__
from megooflags._transport import (
    USER_AGENT,
    Backoff,
    Cancel,
    Cancelled,
    FatalError,
    HttpError,
    RetryAfterError,
    build_headers,
    connection,
    open_request,
    parse_retry_after,
    request,
    sdk_url,
    sleep,
    status_error,
)

KEY = "mf_server_secret-key-123"
NOW = 1_794_657_600.0  # 2026-11-14T12:00:00Z


def test_now_is_the_documented_instant() -> None:
    assert formatdate(NOW, usegmt=True) == "Sat, 14 Nov 2026 12:00:00 GMT"


# Headers and URLs


def test_headers_send_the_key_as_a_bearer_token_with_the_user_agent() -> None:
    assert build_headers(KEY) == {
        "Authorization": f"Bearer {KEY}",
        "User-Agent": f"megooflags-python/{__version__}",
        "Accept": "application/json",
    }
    assert USER_AGENT == f"megooflags-python/{__version__}"


def test_headers_add_content_type_only_with_a_body() -> None:
    assert build_headers(KEY, body=True)["Content-Type"] == "application/json"
    assert "Content-Type" not in build_headers(KEY)


def test_stream_headers_ask_for_an_event_stream_without_caching() -> None:
    h = build_headers(KEY, stream=True)
    assert h["Accept"] == "text/event-stream"
    assert h["Cache-Control"] == "no-cache"
    assert "Content-Type" not in h


@pytest.mark.parametrize(
    ("base", "endpoint", "want"),
    [
        ("https://flags.example.com", "ruleset", "https://flags.example.com/sdk/v1/ruleset"),
        ("https://flags.example.com/", "stream", "https://flags.example.com/sdk/v1/stream"),
        (
            "https://flags.example.com/base//",
            "events",
            "https://flags.example.com/base/sdk/v1/events",
        ),
    ],
)
def test_sdk_url_drops_trailing_slashes(base: str, endpoint: str, want: str) -> None:
    url = sdk_url(base, endpoint)
    assert url == want
    assert KEY not in url


# Status mapping


@pytest.mark.parametrize("status", [200, 204, 304])
def test_success_statuses_are_not_errors(status: int) -> None:
    assert status_error(status, {}, NOW) is None


@pytest.mark.parametrize("status", [401, 403])
def test_refused_key_is_fatal(status: int) -> None:
    err = status_error(status, {}, NOW)
    assert isinstance(err, FatalError)
    assert err.status == status
    assert str(err) == f"megooflags: SDK key refused ({status})"


def test_429_carries_retry_after() -> None:
    err = status_error(429, {"Retry-After": "7"}, NOW)
    assert isinstance(err, RetryAfterError)
    assert err.after == 7.0
    assert str(err) == "megooflags: rate limited, retry after 7s"


def test_429_without_retry_after_waits_0() -> None:
    err = status_error(429, {}, NOW)
    assert isinstance(err, RetryAfterError)
    assert err.after == 0.0


@pytest.mark.parametrize("status", [404, 413, 422, 500, 503])
def test_other_statuses_are_http_errors(status: int) -> None:
    err = status_error(status, {}, NOW)
    assert isinstance(err, HttpError)
    assert err.status == status
    assert str(err) == f"megooflags: HTTP {status}"


def test_revoked_is_fatal() -> None:
    err = FatalError(reason="revoked")
    assert str(err) == "megooflags: SDK key refused (revoked)"
    assert err.status is None
    assert err.reason == "revoked"


def test_status_error_reads_http_message_headers_case_insensitively() -> None:
    msg = http.client.HTTPMessage()
    msg["retry-after"] = "3"
    err = status_error(429, msg, NOW)
    assert isinstance(err, RetryAfterError)
    assert err.after == 3.0


# Retry-After


@pytest.mark.parametrize(
    ("value", "want"),
    [
        ("5", 5.0),
        ("0", 0.0),
        ("-1", 0.0),
        (" 7 ", 7.0),
        (formatdate(NOW + 30, usegmt=True), 30.0),
        (formatdate(NOW - 3600, usegmt=True), 0.0),
        ("Saturday, 14-Nov-26 12:00:30 GMT", 30.0),
        ("Sat Nov 14 12:00:30 2026", 30.0),
    ],
)
def test_parse_retry_after(value: str, want: float) -> None:
    assert parse_retry_after(value, NOW) == want


@pytest.mark.parametrize(
    "value",
    ["abc", "", "1.5", "5s", "Sat, 31 Feb 2026 12:00:30 GMT", "Sat, 14 Nov 2026", None],
)
def test_parse_retry_after_invalid_is_none(value: str | None) -> None:
    assert parse_retry_after(value, NOW) is None


# Backoff and sleep


def test_backoff_doubles_from_base_up_to_max() -> None:
    b = Backoff(3.0, 60.0, rand=lambda: 1.0)
    assert [b.next() for _ in range(7)] == [3.0, 6.0, 12.0, 24.0, 48.0, 60.0, 60.0]


def test_backoff_reset_starts_over() -> None:
    b = Backoff(3.0, 60.0, rand=lambda: 1.0)
    b.next()
    b.next()
    b.reset()
    assert b.next() == 3.0


def test_backoff_jitters_from_the_second_wait() -> None:
    b = Backoff(3.0, 60.0, rand=lambda: 0.0)
    assert b.next() == 3.0
    assert b.next() == pytest.approx(4.8)
    assert b.next() == pytest.approx(9.6)


def test_backoff_first_wait_is_never_above_max() -> None:
    assert Backoff(90.0, 60.0).next() == 60.0


def test_backoff_never_goes_above_max() -> None:
    b = Backoff(3.0, 60.0)
    for _ in range(200):
        assert 0 < b.next() <= 60.0


def test_backoff_base_can_be_replaced() -> None:
    b = Backoff(3.0, 60.0, rand=lambda: 1.0)
    b.base = 1.0
    assert b.next() == 1.0
    assert b.next() == 2.0


def test_sleep_returns_true_after_the_wait() -> None:
    assert sleep(0.001, threading.Event()) is True


def test_sleep_ends_early_with_false_when_cancelled() -> None:
    stop = threading.Event()
    threading.Timer(0.01, stop.set).start()
    start = time.monotonic()
    assert sleep(3600, stop) is False
    assert time.monotonic() - start < 5


def test_sleep_returns_false_at_once_when_already_cancelled() -> None:
    stop = threading.Event()
    stop.set()
    assert sleep(3600, stop) is False


# Requests


@pytest.fixture
def server() -> Iterator[FakeServer]:
    with FakeServer() as s:
        yield s


def test_request_sends_headers_and_body_and_returns_the_response(server: FakeServer) -> None:
    server.reply("/sdk/v1/events", Scripted(202, {"X-Test": "yes"}, b"ok"))
    resp = request(
        "POST",
        sdk_url(server.url, "events"),
        headers=build_headers(KEY, body=True),
        body=b'{"a":1}',
        timeout=5,
    )
    assert resp.status == 202
    assert resp.headers.get("x-test") == "yes"
    assert resp.body == b"ok"
    (got,) = server.received("/sdk/v1/events")
    assert got.method == "POST"
    assert got.body == b'{"a":1}'
    assert got.headers["authorization"] == f"Bearer {KEY}"
    assert got.headers["user-agent"] == USER_AGENT
    assert got.headers["content-type"] == "application/json"
    assert KEY not in got.url


def test_request_keeps_the_query_string(server: FakeServer) -> None:
    server.reply("/sdk/v1/ruleset", Scripted(200, body=b"{}"))
    request("GET", f"{server.url}/sdk/v1/ruleset?x=1", headers={}, timeout=5)
    assert server.received("/sdk/v1/ruleset")[0].url == "/sdk/v1/ruleset?x=1"


def test_request_times_out(server: FakeServer) -> None:
    def slow(_: Received) -> Scripted:
        time.sleep(1)
        return Scripted(200)

    server.reply("/slow", slow)
    with pytest.raises((TimeoutError, socket.timeout)):
        request("GET", f"{server.url}/slow", headers={}, timeout=0.1)


def test_open_request_returns_the_connection_and_the_unread_response(server: FakeServer) -> None:
    server.reply("/sdk/v1/ruleset", Scripted(200, body=b"body"))
    conn, resp = open_request("GET", sdk_url(server.url, "ruleset"), headers={}, timeout=5)
    try:
        assert resp.status == 200
        assert resp.read() == b"body"
    finally:
        conn.close()


def test_connection_is_chosen_by_scheme() -> None:
    plain, path = connection("http://127.0.0.1:1/a?b=c", timeout=1)
    assert type(plain) is http.client.HTTPConnection
    assert path == "/a?b=c"
    secure, path = connection("https://flags.example.com", timeout=1)
    assert isinstance(secure, http.client.HTTPSConnection)
    assert path == "/"
    assert secure.timeout == 1


def test_connection_refuses_other_schemes() -> None:
    with pytest.raises(ValueError, match="http or https"):
        connection("ftp://flags.example.com/x", timeout=1)


# Cancel


def test_cancel_open_returns_the_response_and_closes_it_after(server: FakeServer) -> None:
    server.reply("/sdk/v1/ruleset", Scripted(200, body=b"body"))
    cancel = Cancel()
    with cancel.open("GET", sdk_url(server.url, "ruleset"), headers={}, timeout=5) as resp:
        assert resp.status == 200
        assert resp.read() == b"body"
    assert resp.isclosed()
    assert not cancel.is_set()


def test_cancel_ends_a_blocked_read_on_another_thread(server: FakeServer) -> None:
    server.reply("/sdk/v1/stream", Stream())
    cancel = Cancel()
    got: list[bytes] = []
    errors: list[BaseException] = []

    def read() -> None:
        try:
            with cancel.open("GET", sdk_url(server.url, "stream"), headers={}, timeout=30) as r:
                got.append(r.readline())
        except BaseException as err:
            errors.append(err)

    reader = threading.Thread(target=read)
    reader.start()
    server.next_stream()
    started = time.monotonic()
    cancel.cancel()
    reader.join(5)
    assert not reader.is_alive()
    assert time.monotonic() - started < 1
    assert cancel.is_set()
    assert got == [b""] or isinstance(errors[0], OSError)


def test_cancel_refuses_a_request_once_set(server: FakeServer) -> None:
    cancel = Cancel()
    cancel.cancel()
    cancel.cancel()
    with (
        pytest.raises(Cancelled),
        cancel.open("GET", sdk_url(server.url, "ruleset"), headers={}, timeout=5),
    ):
        pass
    assert server.requests == []


def test_cancel_wait_returns_early_when_cancelled() -> None:
    cancel = Cancel()
    assert cancel.wait(0.01) is False
    threading.Timer(0.05, cancel.cancel).start()
    started = time.monotonic()
    assert cancel.wait(5) is True
    assert time.monotonic() - started < 1

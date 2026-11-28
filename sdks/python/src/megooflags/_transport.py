"""HTTP helpers shared by the ruleset download, the stream and the event sender: request
headers, status mapping, Retry-After parsing, jittered backoff and a small http.client
request helper, with a Cancel that ends a worker's request in flight."""

from __future__ import annotations

import contextlib
import http.client
import random
import re
import socket
import threading
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC
from email.utils import parsedate_to_datetime
from typing import Literal, Protocol
from urllib.parse import urlsplit

from megooflags._version import __version__

USER_AGENT = f"megooflags-python/{__version__}"

Endpoint = Literal["ruleset", "stream", "events"]


class FatalError(Exception):
    """The server refused the SDK key (401, 403, or the stream's revoked event). Retrying with
    the same key cannot succeed."""

    def __init__(self, status: int | None = None, reason: str | None = None) -> None:
        self.status = status
        self.reason = reason
        cause = str(status) if status is not None else reason
        super().__init__(f"megooflags: SDK key refused ({cause})")


class RetryAfterError(Exception):
    """A 429. after is the server's Retry-After in seconds, or 0."""

    def __init__(self, after: float) -> None:
        self.after = after
        super().__init__(f"megooflags: rate limited, retry after {after:g}s")


class HttpError(Exception):
    """Any other unexpected status."""

    def __init__(self, status: int) -> None:
        self.status = status
        super().__init__(f"megooflags: HTTP {status}")


def build_headers(sdk_key: str, *, body: bool = False, stream: bool = False) -> dict[str, str]:
    """The headers of an SDK request. The key travels in the Authorization header only, never in
    the URL. body adds Content-Type; stream asks for text/event-stream without caching."""
    h = {
        "Authorization": f"Bearer {sdk_key}",
        "User-Agent": USER_AGENT,
        "Accept": "text/event-stream" if stream else "application/json",
    }
    if stream:
        h["Cache-Control"] = "no-cache"
    if body:
        h["Content-Type"] = "application/json"
    return h


def sdk_url(base_url: str, endpoint: Endpoint) -> str:
    """The base URL, without its trailing slashes, joined with an SDK endpoint."""
    return f"{base_url.rstrip('/')}/sdk/v1/{endpoint}"


class HeaderReader(Protocol):
    """The part of a header mapping (dict or http.client.HTTPMessage) status_error reads."""

    def get(self, name: str, /) -> str | None: ...


def status_error(status: int, headers: HeaderReader, now: float) -> Exception | None:
    """Map a response status to an error: None for 2xx and 304. now is time.time(), for an
    HTTP-date Retry-After."""
    if 200 <= status < 300 or status == 304:
        return None
    if status in (401, 403):
        return FatalError(status)
    if status == 429:
        after = parse_retry_after(headers.get("Retry-After"), now)
        return RetryAfterError(after or 0.0)
    return HttpError(status)


_SECONDS = re.compile(r"[+-]?[0-9]+", re.ASCII)
# asctime, the one HTTP-date form without a zone (RFC 9110 section 5.6.7); it is UTC.
_ASCTIME = re.compile(
    r"[A-Z][a-z]{2} [A-Z][a-z]{2} [ 0-9][0-9] [0-9]{2}:[0-9]{2}:[0-9]{2} [0-9]{4}", re.ASCII
)


def parse_retry_after(value: str | None, now: float) -> float | None:
    """Read a Retry-After value as delta-seconds or an HTTP-date, in seconds. A negative number
    or a date in the past is 0; a missing or invalid value is None."""
    v = (value or "").strip()
    if _SECONDS.fullmatch(v):
        return float(max(int(v), 0))
    if not v:
        return None
    try:
        at = parsedate_to_datetime(v)
    except (TypeError, ValueError, IndexError):
        return None
    if at.tzinfo is None:
        # Any other date without a zone (or with -0000) is not an HTTP-date.
        if not _ASCTIME.fullmatch(v):
            return None
        at = at.replace(tzinfo=UTC)
    return max(at.timestamp() - now, 0.0)


class Backoff:
    """Jittered exponential backoff in seconds. The first wait is min(base, max); the n-th is
    min(max, base * 2^n) scaled by a factor in [0.8, 1.0]. base can be replaced, for the
    stream's retry field."""

    def __init__(self, base: float, max: float, rand: Callable[[], float] = random.random) -> None:
        self.base = base
        self.max = max
        self._rand = rand
        self._n = 0

    def next(self) -> float:
        """The next wait; advances the attempt counter."""
        if self._n == 0:
            self._n = 1
            return min(self.base, self.max)
        d = min(self.max, self.base * (1 << self._n))
        if self._n < 64:
            self._n += 1
        return d * (0.8 + 0.2 * self._rand())

    def reset(self) -> None:
        """Make the next wait base again."""
        self._n = 0


def sleep(seconds: float, stop: threading.Event) -> bool:
    """Wait seconds and return True, or return False as soon as stop is set."""
    return not stop.wait(seconds)


@dataclass(frozen=True)
class Response:
    """A whole response: status, headers and the body bytes."""

    status: int
    headers: http.client.HTTPMessage
    body: bytes


def connection(url: str, *, timeout: float | None) -> tuple[http.client.HTTPConnection, str]:
    """An unopened connection for url (HTTPS by scheme) and the path with its query to request.
    timeout is the socket timeout in seconds (None: none)."""
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ValueError("megooflags: the URL must be http or https with a host")
    cls = http.client.HTTPSConnection if parts.scheme == "https" else http.client.HTTPConnection
    conn = cls(parts.hostname, parts.port, timeout=timeout)
    path = parts.path or "/"
    if parts.query:
        path += "?" + parts.query
    return conn, path


def open_request(
    method: str,
    url: str,
    *,
    headers: dict[str, str],
    body: bytes | None = None,
    timeout: float | None,
) -> tuple[http.client.HTTPConnection, http.client.HTTPResponse]:
    """Send a request and return the connection with the response whose body is not read yet.
    The caller closes the connection. Closing it does not end a read blocked on another thread:
    use Cancel.open for that."""
    conn, path = connection(url, timeout=timeout)
    try:
        conn.request(method, path, body=body, headers=headers)
        return conn, conn.getresponse()
    except BaseException:
        conn.close()
        raise


def request(
    method: str,
    url: str,
    *,
    headers: dict[str, str],
    body: bytes | None = None,
    timeout: float,
) -> Response:
    """Send a request, read the whole response and close the connection."""
    conn, resp = open_request(method, url, headers=headers, body=body, timeout=timeout)
    try:
        return Response(resp.status, resp.headers, resp.read())
    finally:
        conn.close()


class Cancelled(Exception):
    """A request refused because its Cancel was already set."""

    def __init__(self) -> None:
        super().__init__("megooflags: cancelled")


class Cancel:
    """The stop signal of one worker thread. cancel() sets it, which ends wait() at once, and
    shuts down the socket of every request opened through open(), which ends a read blocked on
    it (closing an http.client connection does not). It cannot be reset."""

    def __init__(self) -> None:
        self._event = threading.Event()
        self._lock = threading.Lock()
        self._socks: set[socket.socket] = set()

    def is_set(self) -> bool:
        """True once cancel() was called."""
        return self._event.is_set()

    def wait(self, seconds: float) -> bool:
        """Wait up to seconds; True when cancelled (at once if it already was)."""
        return self._event.wait(seconds)

    def cancel(self) -> None:
        """Set the signal and end every open request. Idempotent; any thread may call it."""
        with self._lock:
            self._event.set()
            socks = list(self._socks)
        for sock in socks:
            with contextlib.suppress(OSError):
                sock.shutdown(socket.SHUT_RDWR)

    @contextlib.contextmanager
    def open(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        body: bytes | None = None,
        timeout: float | None,
    ) -> Iterator[http.client.HTTPResponse]:
        """Send a request and yield the response whose body is not read yet; the connection is
        closed on exit. Raises Cancelled when the signal is already set. After cancel() a read
        returns what it has (often b"") or raises OSError."""
        conn, path = connection(url, timeout=timeout)
        resp: http.client.HTTPResponse | None = None
        sock: socket.socket | None = None
        try:
            conn.connect()
            sock = conn.sock
            with self._lock:
                if self._event.is_set():
                    raise Cancelled()
                self._socks.add(sock)
            conn.request(method, path, body=body, headers=headers)
            resp = conn.getresponse()
            yield resp
        finally:
            if sock is not None:
                with self._lock:
                    self._socks.discard(sock)
            if resp is not None:
                resp.close()
            conn.close()

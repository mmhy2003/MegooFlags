"""The SSE client for GET /sdk/v1/stream: reconnects with backoff until it is cancelled or the
server refuses the key."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable

from megooflags._delivery.sse import SseEvent, read_sse
from megooflags._transport import (
    Backoff,
    Cancel,
    FatalError,
    HttpError,
    RetryAfterError,
    build_headers,
    status_error,
)


class DeadConnectionError(Exception):
    """The stream sent nothing for the dead timeout."""

    def __init__(self, seconds: float) -> None:
        super().__init__(f"megooflags: stream sent nothing for {seconds:g}s (dead connection)")


class StreamEndedError(Exception):
    """The server closed the stream."""

    def __init__(self) -> None:
        super().__init__("megooflags: stream ended")


def _noop(*_: object) -> None:
    pass


class Streamer:
    """Follows the ruleset stream on the calling thread (see run).

    on_put receives each put event and returns False for an invalid ruleset; on_connected runs
    on each 200; on_disconnected receives the error and the failures in a row before each wait.
    The stream socket has a dead_timeout read timeout (45 s), so a silent connection fails as
    dead; any received bytes, pings included, restart it. A connection that lasted stable_after
    (60 s) resets the backoff and the failure count. backoff is 3 s to 60 s by default; the
    server's retry field replaces its base."""

    def __init__(
        self,
        *,
        url: str,
        sdk_key: str,
        logger: logging.Logger | None = None,
        last_event_id: Callable[[], str] = lambda: "",
        on_put: Callable[[str, str], bool] = lambda raw, event_id: True,
        on_connected: Callable[[], None] = _noop,
        on_disconnected: Callable[[Exception, int], None] = _noop,
        dead_timeout: float = 45.0,
        stable_after: float = 60.0,
        backoff: Backoff | None = None,
    ) -> None:
        self._url = url
        self._sdk_key = sdk_key
        self._logger = logger or logging.getLogger("megooflags")
        self._last_event_id = last_event_id
        self._on_put = on_put
        self._on_connected = on_connected
        self._on_disconnected = on_disconnected
        self._dead_timeout = dead_timeout
        self._stable_after = stable_after
        self._backoff = backoff or Backoff(3.0, 60.0)

    def run(self, cancel: Cancel) -> FatalError | None:
        """Stream until cancel is set, returning None, or until the server refuses the key,
        returning the FatalError. It raises nothing: other errors reconnect."""
        failures = 0
        while True:
            connected_at, err = self._connect(cancel)
            if cancel.is_set():
                return None
            if isinstance(err, FatalError):
                return err
            if connected_at is not None and time.monotonic() - connected_at >= self._stable_after:
                self._backoff.reset()
                failures = 0
            failures += 1
            wait = self._backoff.next()
            if isinstance(err, RetryAfterError):
                wait = max(err.after, wait)
            self._logger.debug(
                "megooflags: stream disconnected (%s), failures %d, retry in %.3fs",
                err,
                failures,
                wait,
            )
            try:
                self._on_disconnected(err, failures)
            except Exception:
                self._logger.exception("megooflags: stream callback failed")
            if cancel.wait(wait):
                return None

    def _connect(self, cancel: Cancel) -> tuple[float | None, Exception]:
        """Open one stream and read it until it ends. The first value is the monotonic time of
        the 200, None when the server did not answer 200."""
        headers = build_headers(self._sdk_key, stream=True)
        event_id = self._last_event_id()
        if event_id:
            headers["Last-Event-ID"] = event_id
        connected_at: float | None = None
        try:
            with cancel.open("GET", self._url, headers=headers, timeout=self._dead_timeout) as resp:
                bad = status_error(resp.status, resp.headers, time.time())
                if bad is None and resp.status != 200:
                    bad = HttpError(resp.status)
                if bad is not None:
                    return connected_at, bad
                connected_at = time.monotonic()
                self._on_connected()
                read_sse(resp, on_line=_noop, on_retry=self._on_retry, on_event=self._on_event)
                return connected_at, StreamEndedError()
        except TimeoutError:
            return connected_at, DeadConnectionError(self._dead_timeout)
        except Exception as err:
            return connected_at, err

    def _on_retry(self, ms: int) -> None:
        self._backoff.base = ms / 1000

    def _on_event(self, ev: SseEvent) -> None:
        if ev.name == "put":
            if not self._on_put(ev.data, ev.id):
                self._logger.warning(
                    "megooflags: stream sent an invalid ruleset (event id %r); "
                    "keeping the previous one",
                    ev.id,
                )
        elif ev.name == "revoked":
            raise FatalError(reason="revoked")

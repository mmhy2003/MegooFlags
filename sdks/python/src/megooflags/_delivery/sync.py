"""Keeps the Store current: the stream, with polling while the stream keeps failing (or polling
only, with disable_stream)."""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from megooflags._delivery.cache import Cache
from megooflags._delivery.poller import Poller
from megooflags._delivery.store import Snapshot, Source, Store
from megooflags._delivery.streamer import Streamer
from megooflags._engine import parse_ruleset
from megooflags._transport import Backoff, Cancel, FatalError, RetryAfterError, sdk_url

# ready: the stream is connected, or with disable_stream the last poll succeeded. stale: the
# stream or the poll failed; the last ruleset is still served. fatal: the key was refused and
# Sync has stopped.
Status = Literal["ready", "stale", "fatal"]

# How long stop() waits for each worker thread.
_JOIN_TIMEOUT = 2.0


@dataclass(frozen=True)
class Tuning:
    """Shorter timings; only tests set it. poll_interval replaces the poll interval without any
    clamp; poll_after_failures is the stream failures in a row before polling."""

    dead_timeout: float = 45.0
    stable_after: float = 60.0
    retry_base: float = 3.0
    retry_max: float = 60.0
    poll_after_failures: int = 2
    poll_interval: float | None = None


def _noop(*_: object) -> None:
    pass


class Sync:
    """Runs the stream and the poll loop on daemon threads. Callbacks run on those threads (or
    on the caller's, for apply) one at a time; no callback runs after stop() has returned or
    after a fatal status. A callback must not wait for another thread that calls into Sync."""

    def __init__(
        self,
        *,
        base_url: str,
        sdk_key: str,
        store: Store,
        cache: Cache | None = None,
        logger: logging.Logger | None = None,
        disable_stream: bool = False,
        poll_interval: float = 30.0,
        on_change: Callable[[Snapshot | None, Snapshot], None] = _noop,
        on_status: Callable[[Status, Exception | None], None] = _noop,
        tuning: Tuning | None = None,
    ) -> None:
        t = tuning or Tuning()
        self._logger = logger or logging.getLogger("megooflags")
        self._disable_stream = disable_stream
        self._poll_interval = t.poll_interval if t.poll_interval is not None else poll_interval
        self._poll_after_failures = t.poll_after_failures
        self._store = store
        self._cache = cache
        self._on_change = on_change
        self._on_status = on_status

        # _lock serialises apply, the status callbacks and the worker bookkeeping.
        self._lock = threading.RLock()
        self._logged: set[str] = set()
        self._started = False
        self._stopped = False
        self._fatal = False
        self._stream_cancel = Cancel()
        self._poll_cancel: Cancel | None = None
        self._threads: list[threading.Thread] = []
        # The earliest next poll (time.monotonic()); kept across restarts of the poll loop.
        self._next_poll = float("-inf")

        self._poller = Poller(
            url=sdk_url(base_url, "ruleset"),
            sdk_key=sdk_key,
            etag=self._etag,
            on_ruleset=lambda raw, etag: self.apply(raw, etag, "poll") is None,
        )
        self._streamer = Streamer(
            url=sdk_url(base_url, "stream"),
            sdk_key=sdk_key,
            logger=self._logger,
            last_event_id=self._etag,
            on_put=lambda raw, event_id: self.apply(raw, event_id, "stream") is None,
            on_connected=self._connected,
            on_disconnected=self._disconnected,
            dead_timeout=t.dead_timeout,
            stable_after=t.stable_after,
            backoff=Backoff(t.retry_base, t.retry_max),
        )

    @property
    def _halted(self) -> bool:
        return self._fatal or self._stopped

    def _etag(self) -> str:
        snap = self._store.load()
        return snap.etag if snap is not None else ""

    def apply(self, raw: str | bytes, etag: str, source: Source) -> Exception | None:
        """Parse raw and install it. An invalid ruleset is logged and returned, and the store
        keeps the previous one. A ruleset from the stream or a poll is also saved to the cache;
        a save error is only logged. Once stopped, a ruleset from the stream or a poll is
        ignored."""
        live = source in ("stream", "poll")
        with self._lock:
            if live and self._halted:
                return None
            try:
                ruleset = parse_ruleset(raw)
            except ValueError as err:
                self._logger.warning(
                    "megooflags: invalid ruleset from %s; keeping the previous one: %s",
                    source,
                    err,
                )
                return err
            new = Snapshot(ruleset=ruleset, etag=etag, source=source)
            old = self._store.swap(new)
            if live and self._cache is not None:
                try:
                    self._cache.save(raw, etag)
                except (OSError, ValueError) as err:
                    self._logger.warning("megooflags: could not save the ruleset cache: %s", err)
            for w in ruleset.warnings:
                if w not in self._logged:
                    self._logged.add(w)
                    self._logger.warning("megooflags: ruleset warning: %s", w)
            self._call(self._on_change, old, new)
            return None

    def start(self) -> None:
        """Run the stream, or only the poll loop with disable_stream, and return at once. Only
        the first call does anything, and none after stop()."""
        with self._lock:
            if self._started or self._halted:
                return
            self._started = True
            if self._disable_stream:
                self._start_polling()
            else:
                self._spawn("megooflags-stream", self._stream_loop)

    def stop(self) -> None:
        """Stop the stream and the poll loop, ending a request in flight, and wait briefly for
        their threads. Idempotent and safe before start()."""
        with self._lock:
            self._stopped = True
            threads = list(self._threads)
            self._stop_polling()
        self._stream_cancel.cancel()
        me = threading.current_thread()
        for t in threads:
            if t is not me:
                t.join(_JOIN_TIMEOUT)

    def _spawn(self, name: str, target: Callable[[], None]) -> None:
        self._threads = [t for t in self._threads if t.is_alive()]
        thread = threading.Thread(target=target, name=name, daemon=True)
        self._threads.append(thread)
        thread.start()

    def _call(self, fn: Callable[..., None], *args: object) -> None:
        try:
            fn(*args)
        except Exception:
            self._logger.exception("megooflags: sync callback failed")

    def _status(self, status: Status, err: Exception | None = None) -> None:
        """Report status unless Sync is stopped or has failed."""
        with self._lock:
            if not self._halted:
                self._call(self._on_status, status, err)

    def _fail(self, err: FatalError) -> None:
        """Report a refused key once and stop everything."""
        with self._lock:
            if self._halted:
                return
            self._fatal = True
            self._stop_polling()
            self._stream_cancel.cancel()
            self._call(self._on_status, "fatal", err)

    def _stream_loop(self) -> None:
        try:
            fatal = self._streamer.run(self._stream_cancel)
        except Exception:
            self._logger.exception("megooflags: stream loop failed")
            return
        if fatal is not None:
            self._fail(fatal)

    def _connected(self) -> None:
        with self._lock:
            self._stop_polling()
        self._status("ready")

    def _disconnected(self, err: Exception, failures: int) -> None:
        self._status("stale", err)
        if failures >= self._poll_after_failures:
            with self._lock:
                self._start_polling()

    def _start_polling(self) -> None:
        """Start the poll loop unless it runs; the caller holds _lock."""
        if self._poll_cancel is not None or self._halted:
            return
        cancel = Cancel()
        self._poll_cancel = cancel
        self._spawn("megooflags-poll", lambda: self._poll_loop(cancel))

    def _stop_polling(self) -> None:
        """End the poll loop and its poll in flight; the caller holds _lock."""
        if self._poll_cancel is not None:
            self._poll_cancel.cancel()
            self._poll_cancel = None

    def _poll_loop(self, cancel: Cancel) -> None:
        try:
            self._polls(cancel)
        except Exception:
            self._logger.exception("megooflags: poll loop failed")

    def _polls(self, cancel: Cancel) -> None:
        while not cancel.is_set():
            now = time.monotonic()
            with self._lock:
                wait = self._next_poll - now
                if wait <= 0:
                    self._next_poll = now + self._poll_interval
            if wait > 0:
                if cancel.wait(wait):
                    return
                continue
            err: Exception | None = None
            try:
                self._poller.poll(cancel)
            except Exception as e:
                err = e
            if cancel.is_set():
                return
            if isinstance(err, FatalError):
                self._fail(err)
                return
            if isinstance(err, RetryAfterError):
                with self._lock:
                    self._next_poll = now + max(err.after, self._poll_interval)
            if err is not None:
                self._logger.debug("megooflags: poll failed: %s", err)
            if self._disable_stream:
                if err is None:
                    self._status("ready")
                else:
                    self._status("stale", err)

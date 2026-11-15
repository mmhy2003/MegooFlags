"""Posts evaluation counts and exposures to POST /sdk/v1/events from one daemon thread, which
alone owns the Aggregator."""

from __future__ import annotations

import http.client
import logging
import queue
import threading
import time
from collections.abc import Callable

from megooflags._events.aggregate import Aggregator, Batch, Evaluation
from megooflags._transport import (
    Backoff,
    Cancel,
    Cancelled,
    FatalError,
    HttpError,
    RetryAfterError,
    build_headers,
    status_error,
)

# The largest events body the server accepts.
MAX_BODY_BYTES = 1_000_000

# The socket timeout of one POST.
_SEND_TIMEOUT = 10.0

# How long stop() waits for the thread.
_JOIN_TIMEOUT = 2.0

# Put on the queue to wake the thread for stop() or shutdown().
_WAKE = object()


def _noop(_: Exception) -> None:
    pass


class Batcher:
    """record() never blocks: it queues the evaluation, and a full queue, or a Batcher that was
    stopped or shut down, drops it and counts the drop. The thread flushes every
    flush_interval, when either list reaches max_items, and on shutdown. clock (seconds,
    time.monotonic by default) times the retries; only tests replace it.

    on_fatal runs once, on the batcher's thread, when the server refuses the key (401 or 403);
    sending has stopped for good by then. It may call stop() and shutdown()."""

    def __init__(
        self,
        *,
        url: str,
        sdk_key: str,
        logger: logging.Logger | None = None,
        flush_interval: float = 30.0,
        queue_size: int = 10_000,
        dedup_size: int = 10_000,
        max_pending: int = 10_000,
        max_items: int = 1000,
        send_context: bool = False,
        on_fatal: Callable[[Exception], None] = _noop,
        clock: Callable[[], float] = time.monotonic,
        retry_base: float = 2.0,
        retry_max: float = 60.0,
    ) -> None:
        self._url = url
        self._sdk_key = sdk_key
        self._logger = logger or logging.getLogger("megooflags")
        self._flush_interval = flush_interval
        self._max_items = max_items
        self._on_fatal = on_fatal
        self._clock = clock
        self._queue: queue.Queue[Evaluation | object] = queue.Queue(maxsize=queue_size)
        self._agg = Aggregator(
            dedup_size=dedup_size, max_pending=max_pending, send_context=send_context
        )
        self._backoff = Backoff(retry_base, retry_max)
        # Ends a POST in flight on stop() or past the shutdown deadline.
        self._cancel = Cancel()

        self._lock = threading.Lock()
        self._dropped = 0
        self._closed = False  # record() drops from now on
        self._stopping = False
        self._shutdown_at: float | None = None  # time.monotonic() deadline, once requested
        self._thread: threading.Thread | None = None
        self._started = False
        self._result = True  # what shutdown() returns once the thread has ended

        # Owned by the thread.
        self._next_attempt = float("-inf")
        self._fatal = False

    @property
    def dropped(self) -> int:
        """How many evaluations and exposures were dropped."""
        return self._dropped + self._agg.dropped

    def start(self) -> None:
        """Run the thread. Only the first call does anything, and none after stop() or
        shutdown()."""
        with self._lock:
            if self._started or self._closed:
                return
            self._started = True
            self._thread = threading.Thread(target=self._run, name="megooflags-events", daemon=True)
            self._thread.start()

    def record(self, ev: Evaluation) -> None:
        """Queue ev without blocking."""
        if not self._closed:
            try:
                self._queue.put_nowait(ev)
                return
            except queue.Full:
                pass
        with self._lock:
            self._dropped += 1

    def shutdown(self, deadline: float = 5.0) -> bool:
        """Stop accepting, then send what is buffered until it is empty or deadline seconds have
        passed, waiting out a retry delay when it fits. True when everything buffered was
        delivered (or refused by the server); False after the deadline, a stop() or a refused
        key. Idempotent."""
        with self._lock:
            self._closed = True
            thread = self._thread
            if thread is not None and self._shutdown_at is None:
                self._shutdown_at = time.monotonic() + deadline
            at = self._shutdown_at
        if thread is None:
            return self._result
        self._wake()
        if thread is threading.current_thread():
            return False
        assert at is not None
        thread.join(max(at - time.monotonic(), 0.0))
        if thread.is_alive():
            self._cancel.cancel()
            thread.join(_JOIN_TIMEOUT)
            return False
        return self._result

    def stop(self) -> None:
        """Stop without sending what is buffered, ending a POST in flight, and wait briefly for
        the thread. Idempotent and safe before start()."""
        with self._lock:
            self._closed = True
            self._stopping = True
            thread = self._thread
            if thread is None:
                self._result = False
        self._cancel.cancel()
        self._wake()
        if thread is not None and thread is not threading.current_thread():
            thread.join(_JOIN_TIMEOUT)

    def _wake(self) -> None:
        # A full queue needs no wake-up: the thread is busy with it and checks the flags.
        try:
            self._queue.put_nowait(_WAKE)
        except queue.Full:
            pass

    @property
    def _halted(self) -> bool:
        return self._stopping or self._fatal

    def _run(self) -> None:
        try:
            self._loop()
        except Exception:
            self._result = False
            self._logger.exception("megooflags: the events thread failed")

    def _loop(self) -> None:
        next_flush = time.monotonic() + self._flush_interval
        while not self._halted:
            if self._shutdown_at is not None:
                self._drain()
                return
            try:
                item = self._queue.get(timeout=max(next_flush - time.monotonic(), 0.0))
            except queue.Empty:
                item = None
            if isinstance(item, Evaluation):
                self._agg.add(item)
                if max(self._agg.pending()) >= self._max_items:
                    self._flush()
            if time.monotonic() >= next_flush:
                self._flush()
                next_flush = time.monotonic() + self._flush_interval
        self._result = False

    def _drain(self) -> None:
        """Empty the queue, then send everything until the shutdown deadline, waiting out a
        retry delay when it fits."""
        assert self._shutdown_at is not None
        while True:
            try:
                item = self._queue.get_nowait()
            except queue.Empty:
                break
            if isinstance(item, Evaluation):
                self._agg.add(item)
        while not self._halted:
            if self._agg.pending() == (0, 0):
                return
            left = self._shutdown_at - time.monotonic()
            wait = self._next_attempt - self._clock()
            if left <= 0 or wait > left or (wait > 0 and self._cancel.wait(wait)):
                self._result = False
                return
            self._flush()
        self._result = False

    def _flush(self) -> None:
        """Send what is pending in batches of at most max_items per list. Does nothing before
        the next attempt and stops at the first failure."""
        if self._clock() < self._next_attempt:
            return
        while not self._halted and self._agg.pending() != (0, 0):
            unsent = self._send(self._agg.take(self._max_items))
            if unsent is not None:
                self._agg.restore(unsent)
                return

    def _send(self, batch: Batch) -> Batch | None:
        """Post batch, splitting it when it is too large. Returns the part that was not
        delivered and must be retried, or None."""
        try:
            body = batch.body()
        except (TypeError, ValueError) as bad:
            self._logger.error("megooflags: cannot encode events; dropping the batch: %s", bad)
            return None
        if len(body) > MAX_BODY_BYTES:
            return self._split(batch, "the body is over 1 MB")

        try:
            with self._cancel.open(
                "POST",
                self._url,
                headers=build_headers(self._sdk_key, body=True),
                body=body,
                timeout=_SEND_TIMEOUT,
            ) as resp:
                resp.read(64 << 10)
                status, headers = resp.status, resp.headers
        except Cancelled:
            return batch
        except (OSError, http.client.HTTPException) as net:
            if self._cancel.is_set():
                return batch
            self._retry_in(self._backoff.next())
            self._logger.debug("megooflags: sending events failed; will retry: %s", net)
            return batch

        err = status_error(status, headers, time.time())
        if err is None:
            self._backoff.reset()
            return None
        if isinstance(err, FatalError):
            self._fatal = True
            with self._lock:
                self._closed = True
                self._result = False
            try:
                self._on_fatal(err)
            except Exception:
                self._logger.exception("megooflags: events on_fatal callback failed")
            return None
        if isinstance(err, RetryAfterError):
            self._retry_in(max(err.after, self._backoff.next()))
            self._logger.debug("megooflags: events rate limited; retry after %gs", err.after)
            return batch
        if isinstance(err, HttpError) and err.status == 413:
            return self._split(batch, "the server refused the body as too large")
        if isinstance(err, HttpError) and err.status >= 500:
            self._retry_in(self._backoff.next())
            self._logger.debug("megooflags: sending events failed; will retry: %s", err)
            return batch
        self._logger.warning(
            "megooflags: the server refused an events batch; dropping it (%s, %d exposures, "
            "%d counts)",
            err,
            len(batch.exposures),
            len(batch.counts),
        )
        return None

    def _split(self, batch: Batch, why: str) -> Batch | None:
        """Send batch as two halves, exposures first. A single item cannot be split and is
        dropped. When the first half must be retried, the second is kept unsent."""
        if len(batch) <= 1:
            self._logger.warning("megooflags: an events item is too large; dropping it (%s)", why)
            return None
        half = len(batch) // 2
        ne = min(half, len(batch.exposures))
        nc = half - ne
        first = Batch(batch.exposures[:ne], batch.counts[:nc])
        second = Batch(batch.exposures[ne:], batch.counts[nc:])
        unsent = self._send(first)
        if self._halted:
            return None
        if unsent is not None:
            return Batch(unsent.exposures + second.exposures, unsent.counts + second.counts)
        return self._send(second)

    def _retry_in(self, seconds: float) -> None:
        self._next_attempt = self._clock() + seconds

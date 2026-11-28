import json
import logging
import threading
import time
from collections.abc import Callable, Iterator
from typing import Any

import pytest
from fake_server import FakeServer, Received, Scripted
from waiting import wait_for

from megooflags._events.aggregate import Evaluation
from megooflags._events.batcher import Batcher
from megooflags._transport import USER_AGENT, FatalError, sdk_url

KEY = "mf_server_batcher-key-value"
EVENTS = "/sdk/v1/events"


class FakeClock:
    """A clock the test moves by hand; it starts at 1000 s."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def server(no_thread_leaks: None) -> Iterator[FakeServer]:
    with FakeServer() as s:
        yield s


@pytest.fixture
def new_batcher(server: FakeServer) -> Iterator[Callable[..., Batcher]]:
    made: list[Batcher] = []

    def make(**kw: Any) -> Batcher:
        opts: dict[str, Any] = {
            "url": sdk_url(server.url, "events"),
            "sdk_key": KEY,
            "flush_interval": 0.05,
            "retry_base": 0.01,
            "retry_max": 0.04,
        }
        opts.update(kw)
        b = Batcher(**opts)
        made.append(b)
        b.start()
        return b

    yield make
    for b in made:
        b.stop()


def ev(flag: str, ctx_key: str, context: dict[str, Any] | None = None) -> Evaluation:
    return Evaluation(
        flag_key=flag,
        variant="on",
        served=True,
        reason="STATIC",
        context_key=ctx_key,
        context=context,
        at=int(time.time() * 1000),
    )


def body(r: Received) -> dict[str, Any]:
    got: dict[str, Any] = json.loads(r.body)
    return got


def items(b: dict[str, Any]) -> int:
    return len(b["exposures"]) + len(b["counts"])


def totals(batches: list[dict[str, Any]]) -> tuple[int, dict[str, int]]:
    """The exposures and the count per flag in batches."""
    counts: dict[str, int] = {}
    exposures = 0
    for b in batches:
        exposures += len(b["exposures"])
        for c in b["counts"]:
            counts[c["flag_key"]] = counts.get(c["flag_key"], 0) + c["count"]
    return exposures, counts


def respond(server: FakeServer, status: Callable[[dict[str, Any], int], int]) -> None:
    """Answer each events request with status(body, n), n counting from 1."""
    n = 0
    lock = threading.Lock()

    def reply(r: Received) -> Scripted:
        nonlocal n
        with lock:
            n += 1
            i = n
        return Scripted(status(body(r), i))

    server.reply(EVENTS, reply)


def accept(server: FakeServer) -> None:
    server.reply(EVENTS, Scripted(202))


def test_posts_json_with_the_bearer_key_and_the_user_agent(
    server: FakeServer, new_batcher: Callable[..., Batcher]
) -> None:
    accept(server)
    b = new_batcher()
    b.record(ev("f", "u1"))
    wait_for(lambda: len(server.received(EVENTS)) == 1)
    r = server.received(EVENTS)[0]
    assert r.method == "POST"
    assert r.headers["authorization"] == f"Bearer {KEY}"
    assert r.headers["user-agent"] == USER_AGENT
    assert r.headers["content-type"] == "application/json"
    assert KEY not in r.url
    got = body(r)
    assert [(e["flag_key"], e["variant"]) for e in got["exposures"]] == [("f", "on")]
    assert [(c["flag_key"], c["variant"], c["count"]) for c in got["counts"]] == [("f", "on", 1)]


def test_flushes_on_the_interval(server: FakeServer, new_batcher: Callable[..., Batcher]) -> None:
    accept(server)
    b = new_batcher()
    b.record(ev("f", "u1"))
    assert server.received(EVENTS) == []
    wait_for(lambda: len(server.received(EVENTS)) == 1)
    b.record(ev("g", "u1"))
    wait_for(lambda: len(server.received(EVENTS)) == 2)
    assert totals([body(r) for r in server.received(EVENTS)]) == (2, {"f": 1, "g": 1})


def test_flushes_when_a_list_reaches_1000_items(
    server: FakeServer, new_batcher: Callable[..., Batcher]
) -> None:
    accept(server)
    b = new_batcher(flush_interval=3600)
    for i in range(999):
        b.record(ev("f", f"u{i}"))
    time.sleep(0.1)
    assert server.received(EVENTS) == []
    b.record(ev("f", "u999"))
    wait_for(lambda: len(server.received(EVENTS)) == 1)
    assert len(body(server.received(EVENTS)[0])["exposures"]) == 1000


def test_shutdown_sends_everything_once(
    server: FakeServer, new_batcher: Callable[..., Batcher]
) -> None:
    accept(server)
    b = new_batcher(flush_interval=3600)
    b.record(ev("f", "u1"))
    b.record(ev("f", "u2"))
    b.record(ev("g", "u1"))
    assert b.shutdown(5.0) is True
    assert totals([body(r) for r in server.received(EVENTS)]) == (3, {"f": 2, "g": 1})
    assert b.shutdown(5.0) is True
    assert len(server.received(EVENTS)) == 1


def test_shutdown_gives_up_at_the_deadline(
    server: FakeServer, new_batcher: Callable[..., Batcher]
) -> None:
    release = threading.Event()

    def hang(_: Received) -> Scripted:
        release.wait(10)
        return Scripted(202)

    server.reply(EVENTS, hang)
    try:
        b = new_batcher(flush_interval=3600)
        b.record(ev("f", "u1"))
        start = time.monotonic()
        assert b.shutdown(0.2) is False
        assert time.monotonic() - start < 1.0
    finally:
        release.set()


def test_shutdown_does_not_wait_for_a_retry_delay_past_the_deadline(
    server: FakeServer, new_batcher: Callable[..., Batcher]
) -> None:
    server.reply(EVENTS, Scripted(429, {"Retry-After": "30"}))
    b = new_batcher(flush_interval=3600)
    b.record(ev("f", "u1"))
    start = time.monotonic()
    assert b.shutdown(2.0) is False
    assert time.monotonic() - start < 1.0
    assert len(server.received(EVENTS)) == 1


def test_shutdown_waits_out_a_retry_delay_that_fits(
    server: FakeServer, new_batcher: Callable[..., Batcher]
) -> None:
    server.reply(EVENTS, Scripted(503), Scripted(202))
    b = new_batcher(flush_interval=3600, retry_base=0.2, retry_max=1.0)
    b.record(ev("f", "u1"))
    assert b.shutdown(5.0) is True
    first, second = server.received(EVENTS)
    assert second.at - first.at >= 0.2
    assert body(second) == body(first)


def test_stop_sends_nothing(server: FakeServer, new_batcher: Callable[..., Batcher]) -> None:
    accept(server)
    b = new_batcher(flush_interval=3600)
    b.record(ev("f", "u1"))
    b.stop()
    b.stop()
    assert b.shutdown(5.0) is False
    assert server.received(EVENTS) == []


def test_a_full_queue_drops_and_counts_without_blocking() -> None:
    b = Batcher(url="http://127.0.0.1:9/sdk/v1/events", sdk_key=KEY, queue_size=2)
    start = time.monotonic()
    for i in range(5):
        b.record(ev("f", f"u{i}"))
    assert time.monotonic() - start < 0.1
    assert b.dropped == 3
    b.stop()


def test_records_after_shutdown_or_stop_are_dropped(
    server: FakeServer, new_batcher: Callable[..., Batcher]
) -> None:
    accept(server)
    b = new_batcher()
    assert b.shutdown(5.0) is True
    b.record(ev("f", "u1"))
    b.stop()
    b.record(ev("f", "u2"))
    assert b.dropped == 2
    assert server.received(EVENTS) == []


def test_401_stops_for_good_and_calls_on_fatal_once(
    server: FakeServer, new_batcher: Callable[..., Batcher]
) -> None:
    server.reply(EVENTS, Scripted(401))
    fatals: list[Exception] = []
    b = new_batcher(on_fatal=fatals.append)
    b.record(ev("f", "u1"))
    wait_for(lambda: len(fatals) == 1)
    b.record(ev("g", "u1"))
    time.sleep(0.2)
    assert b.shutdown(5.0) is False
    assert len(server.received(EVENTS)) == 1
    assert len(fatals) == 1
    assert isinstance(fatals[0], FatalError)
    assert fatals[0].status == 401
    assert b.dropped == 1


def test_on_fatal_may_stop_and_shut_down_the_batcher(
    server: FakeServer, new_batcher: Callable[..., Batcher]
) -> None:
    server.reply(EVENTS, Scripted(403))
    results: list[bool] = []

    def on_fatal(_: Exception) -> None:
        b.stop()
        results.append(b.shutdown(5.0))

    b = new_batcher(on_fatal=on_fatal)
    b.record(ev("f", "u1"))
    wait_for(lambda: results == [False])


def test_413_splits_the_batch(server: FakeServer, new_batcher: Callable[..., Batcher]) -> None:
    respond(server, lambda b, _: 413 if items(b) > 10 else 202)
    b = new_batcher(flush_interval=3600)
    for i in range(25):
        b.record(ev("f", f"u{i}"))
    assert b.shutdown(5.0) is True
    accepted = [body(r) for r in server.received(EVENTS) if items(body(r)) <= 10]
    assert all(items(a) > 0 for a in accepted)
    assert totals(accepted) == (25, {"f": 25})


def test_a_body_over_1_mb_is_split_before_sending(
    server: FakeServer, new_batcher: Callable[..., Batcher]
) -> None:
    accept(server)
    b = new_batcher(flush_interval=3600, send_context=True)
    big = {"blob": "x" * 2000}
    for i in range(1000):
        b.record(ev("f", f"u{i}", big))
    assert b.shutdown(5.0) is True
    sent = server.received(EVENTS)
    assert len(sent) >= 2
    assert all(len(r.body) <= 1_000_000 for r in sent)
    assert totals([body(r) for r in sent]) == (1000, {"f": 1000})


def test_a_single_item_that_is_too_large_is_dropped(
    server: FakeServer, new_batcher: Callable[..., Batcher], caplog: pytest.LogCaptureFixture
) -> None:
    accept(server)
    b = new_batcher(flush_interval=3600, send_context=True)
    b.record(ev("f", "u1", {"blob": "x" * 1_100_000}))
    with caplog.at_level(logging.WARNING, logger="megooflags"):
        assert b.shutdown(5.0) is True
    # The exposure is dropped; its count goes on its own.
    sent = [body(r) for r in server.received(EVENTS)]
    assert [(len(s["exposures"]), len(s["counts"])) for s in sent] == [(0, 1)]
    assert "too large" in caplog.text


def test_422_drops_the_batch(
    server: FakeServer, new_batcher: Callable[..., Batcher], caplog: pytest.LogCaptureFixture
) -> None:
    server.reply(EVENTS, Scripted(422))
    with caplog.at_level(logging.WARNING, logger="megooflags"):
        b = new_batcher()
        b.record(ev("f", "u1"))
        wait_for(lambda: len(server.received(EVENTS)) == 1)
        time.sleep(0.2)  # four intervals
        assert b.shutdown(5.0) is True
    assert len(server.received(EVENTS)) == 1
    assert "refused an events batch" in caplog.text


def test_429_resends_after_retry_after(
    server: FakeServer, new_batcher: Callable[..., Batcher]
) -> None:
    server.reply(EVENTS, Scripted(429, {"Retry-After": "30"}), Scripted(202))
    clock = FakeClock()
    b = new_batcher(clock=clock, flush_interval=0.02)
    b.record(ev("f", "u1"))
    wait_for(lambda: len(server.received(EVENTS)) == 1)
    time.sleep(0.1)  # the batcher reads the 429 before the clock moves
    clock.advance(29)
    time.sleep(0.1)
    assert len(server.received(EVENTS)) == 1
    clock.advance(1.5)
    wait_for(lambda: len(server.received(EVENTS)) == 2)
    first, second = server.received(EVENTS)
    assert body(second) == body(first)


def test_5xx_retries_with_growing_backoff_and_keeps_the_counts(
    server: FakeServer, new_batcher: Callable[..., Batcher]
) -> None:
    clock = FakeClock()
    b = new_batcher(clock=clock, flush_interval=0.02, retry_base=2.0, retry_max=60.0)

    def status(_: dict[str, Any], n: int) -> int:
        if n == 1:
            # Recorded while the first batch is in flight: merged on restore.
            for _ in range(3):
                b.record(ev("f", "u1"))
        return 503 if n <= 2 else 202

    respond(server, status)
    for _ in range(5):
        b.record(ev("f", "u1"))
    wait_for(lambda: len(server.received(EVENTS)) == 1)
    time.sleep(0.1)
    assert len(server.received(EVENTS)) == 1
    clock.advance(2.1)  # the first wait is the base, 2 s
    wait_for(lambda: len(server.received(EVENTS)) == 2)
    time.sleep(0.1)  # the batcher reads the 503 before the clock moves
    clock.advance(2.1)  # the second wait is at least 3.2 s
    time.sleep(0.1)
    assert len(server.received(EVENTS)) == 2
    clock.advance(2.1)
    wait_for(lambda: len(server.received(EVENTS)) == 3)
    assert b.shutdown(5.0) is True
    assert totals([body(server.received(EVENTS)[2])]) == (1, {"f": 8})


def test_a_split_stops_after_a_fatal_status(
    server: FakeServer, new_batcher: Callable[..., Batcher]
) -> None:
    respond(server, lambda b, _: 413 if items(b) > 10 else 401)
    b = new_batcher(flush_interval=3600)
    for i in range(25):
        b.record(ev("f", f"u{i}"))
    assert b.shutdown(5.0) is False
    # 26 items: 413; 13: 413; 6: 401, and no request after it.
    assert [items(body(r)) for r in server.received(EVENTS)] == [26, 13, 6]

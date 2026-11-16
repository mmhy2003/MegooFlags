import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

from megooflags_api.schemas.cluster import ClusterEventsIn
from megooflags_api.schemas.events import CountIn, EventsIn, ExposureIn
from megooflags_api.services.worker.buffer import EventBuffer, SendResult
from megooflags_api.services.worker.sources import BufferEventSink

ENV_A = uuid.UUID("00000000-0000-0000-0000-00000000000a")
ENV_B = uuid.UUID("00000000-0000-0000-0000-00000000000b")
START = datetime(2026, 11, 16, 12, 30, tzinfo=UTC)
HOUR = datetime(2026, 11, 16, 12, tzinfo=UTC)
MB = 1024 * 1024


class Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


def new_buffer(
    directory: Path, clock: Clock | None = None, max_bytes: int = MB, memory_items: int = 10_000
) -> EventBuffer:
    buffer = EventBuffer(
        directory, max_bytes, memory_items=memory_items, clock=clock or Clock(START)
    )
    buffer.load()
    return buffer


def exposure(flag_key: str = "new-checkout", context: dict[str, str] | None = None) -> ExposureIn:
    return ExposureIn(
        flag_key=flag_key, variant="on", reason="TARGETING_MATCH", timestamp=START, context=context
    )


def files(directory: Path, pattern: str = "*.json") -> list[Path]:
    return list(directory.glob(pattern))


def batches(directory: Path) -> list[ClusterEventsIn]:
    """The batch files, oldest first."""
    return [
        ClusterEventsIn.model_validate_json(path.read_bytes())
        for path in sorted(directory.glob("*.json"), key=lambda p: p.name.split("-")[1])
    ]


class Sender:
    def __init__(self, results: list[SendResult]) -> None:
        self.results = results
        self.sent: list[ClusterEventsIn] = []

    async def __call__(self, batch: ClusterEventsIn) -> SendResult:
        self.sent.append(batch)
        return self.results.pop(0) if self.results else "accepted"


def test_counts_summed_and_sealed(tmp_path: Path) -> None:
    buffer = new_buffer(tmp_path)
    buffer.add_counts(ENV_A, [("new-checkout", "on"), ("new-checkout", "on"), ("banner", None)])
    buffer.add_count_items(
        ENV_A,
        [
            CountIn(flag_key="new-checkout", variant="on", hour=START, count=5),
            CountIn(flag_key="banner", hour=START + timedelta(hours=1), count=2),
        ],
    )
    assert buffer.pending_events == 3
    assert list(tmp_path.glob("*.json")) == []

    buffer.seal()

    [batch] = batches(tmp_path)
    assert batch.environment_id == ENV_A
    assert batch.exposures == []
    counts = {(c.flag_key, c.variant, c.hour): c.count for c in batch.counts}
    assert counts == {
        ("new-checkout", "on", HOUR): 7,
        ("banner", None, HOUR): 1,
        ("banner", None, HOUR + timedelta(hours=1)): 2,
    }
    assert [p.name.split("-")[0] for p in tmp_path.glob("*.json")] == ["counts"]
    assert buffer.pending_events == 3

    buffer.seal()
    assert len(batches(tmp_path)) == 1


def test_exposures_spill_at_memory_limit(tmp_path: Path) -> None:
    buffer = new_buffer(tmp_path, memory_items=3)
    buffer.add_exposures(ENV_A, [exposure()] * 3, persist_context=False)
    assert list(tmp_path.glob("*.json")) == []

    buffer.add_exposures(ENV_A, [exposure()], persist_context=False)

    [batch] = batches(tmp_path)
    assert len(batch.exposures) == 4
    assert batch.counts == []
    assert list(tmp_path.glob("exposures-*.json"))
    assert buffer.pending_events == 4


def test_context_removed_unless_persist(tmp_path: Path) -> None:
    buffer = new_buffer(tmp_path)
    buffer.add_exposures(ENV_A, [exposure("a", {"user": "u1"})], persist_context=False)
    buffer.add_exposures(ENV_B, [exposure("b", {"user": "u2"})], persist_context=True)
    buffer.seal()

    by_env = {batch.environment_id: batch for batch in batches(tmp_path)}
    assert by_env[ENV_A].exposures[0].context is None
    assert by_env[ENV_B].exposures[0].context == {"user": "u2"}
    assert "u1" not in "".join(p.read_text() for p in tmp_path.glob("*.json"))


def test_drop_oldest_exposures_over_max_bytes(tmp_path: Path) -> None:
    clock = Clock(START)
    buffer = new_buffer(tmp_path, clock, max_bytes=10_000)
    buffer.add_counts(ENV_A, [("banner", None)])
    buffer.seal()
    for i in range(10):
        clock.now += timedelta(seconds=1)
        buffer.add_exposures(ENV_A, [exposure(f"flag-{i}")] * 20, persist_context=False)
        buffer.seal()

    total = sum(p.stat().st_size for p in tmp_path.glob("*.json"))
    assert total <= 10_000
    remaining = batches(tmp_path)
    assert remaining[0].counts and not remaining[0].exposures
    kept = [batch.exposures[0].flag_key for batch in remaining[1:]]
    assert kept == [f"flag-{i}" for i in range(10 - len(kept), 10)]
    assert 0 < len(kept) < 10
    assert buffer.dropped_total == (10 - len(kept)) * 20
    assert buffer.pending_events == 1 + len(kept) * 20


def test_files_older_than_7_days_dropped_on_load(tmp_path: Path) -> None:
    clock = Clock(START)
    buffer = new_buffer(tmp_path, clock)
    buffer.add_exposures(ENV_A, [exposure("old")] * 2, persist_context=False)
    buffer.seal()
    clock.now += timedelta(days=6)
    buffer.add_exposures(ENV_A, [exposure("recent")], persist_context=False)
    buffer.seal()

    clock.now = START + timedelta(days=7, seconds=1)
    reloaded = new_buffer(tmp_path, clock)

    assert [b.exposures[0].flag_key for b in batches(tmp_path)] == ["recent"]
    assert reloaded.pending_events == 1
    assert reloaded.dropped_total == 2


def test_restart_keeps_batches(tmp_path: Path) -> None:
    buffer = new_buffer(tmp_path)
    buffer.add_counts(ENV_A, [("banner", None)])
    buffer.add_exposures(ENV_A, [exposure()] * 2, persist_context=False)
    buffer.seal()
    before = {batch.batch_id for batch in batches(tmp_path)}
    (tmp_path / "broken.json").write_text("{")
    (tmp_path / "counts-1-x.json.tmp").write_text("partial")

    reloaded = new_buffer(tmp_path)

    assert {batch.batch_id for batch in batches(tmp_path)} == before
    assert reloaded.pending_events == 3
    assert not (tmp_path / "broken.json").exists()
    assert list(tmp_path.glob("*.tmp")) == []


async def test_forwarder_oldest_first_and_deletes_on_accept(tmp_path: Path) -> None:
    clock = Clock(START)
    buffer = new_buffer(tmp_path, clock)
    for flag in ("first", "second", "third"):
        buffer.add_exposures(ENV_A, [exposure(flag)], persist_context=False)
        buffer.seal()
        clock.now += timedelta(seconds=1)
    ids = [batch.batch_id for batch in batches(tmp_path)]
    send = Sender([])

    await buffer.forward(send)

    assert [batch.batch_id for batch in send.sent] == ids
    assert [batch.exposures[0].flag_key for batch in send.sent] == ["first", "second", "third"]
    assert files(tmp_path) == []
    assert buffer.pending_events == 0


async def test_forwarder_keeps_on_retry_and_resends_same_batch_id(tmp_path: Path) -> None:
    clock = Clock(START)
    buffer = new_buffer(tmp_path, clock)
    for flag in ("first", "second"):
        buffer.add_exposures(ENV_A, [exposure(flag)], persist_context=False)
        buffer.seal()
        clock.now += timedelta(seconds=1)
    ids = [batch.batch_id for batch in batches(tmp_path)]
    send = Sender(["retry"])

    await buffer.forward(send)

    assert [batch.batch_id for batch in send.sent] == ids[:1]
    assert [batch.batch_id for batch in batches(tmp_path)] == ids
    assert buffer.pending_events == 2

    await buffer.forward(send)

    assert [batch.batch_id for batch in send.sent] == [ids[0], ids[0], ids[1]]
    assert files(tmp_path) == []


async def test_forwarder_deletes_rejected(tmp_path: Path) -> None:
    clock = Clock(START)
    buffer = new_buffer(tmp_path, clock)
    for flag in ("bad", "good"):
        buffer.add_exposures(ENV_A, [exposure(flag)], persist_context=False)
        buffer.seal()
        clock.now += timedelta(seconds=1)
    send = Sender(["rejected"])

    await buffer.forward(send)

    assert [batch.exposures[0].flag_key for batch in send.sent] == ["bad", "good"]
    assert files(tmp_path) == []
    assert buffer.pending_events == 0
    assert buffer.dropped_total == 0


def test_batches_at_most_1000_items(tmp_path: Path) -> None:
    buffer = new_buffer(tmp_path, max_bytes=64 * MB)
    buffer.add_exposures(ENV_A, [exposure()] * 2500, persist_context=False)
    buffer.add_counts(ENV_A, [(f"flag-{i}", None) for i in range(1001)])
    buffer.seal()

    found = batches(tmp_path)
    assert sorted(len(b.exposures) for b in found if b.exposures) == [500, 1000, 1000]
    assert sorted(len(b.counts) for b in found if b.counts) == [1, 1000]
    assert all(not (b.exposures and b.counts) for b in found)
    assert len({b.batch_id for b in found}) == 5
    assert buffer.pending_events == 3501


async def test_sink_records_counts_and_events(tmp_path: Path) -> None:
    buffer = new_buffer(tmp_path)
    persist = {"value": False}
    sink = BufferEventSink(buffer, lambda: persist["value"])
    future = START + timedelta(hours=1)
    body = EventsIn(
        exposures=[
            ExposureIn(flag_key="a", reason="DEFAULT", timestamp=future, context={"user": "u1"})
        ],
        counts=[CountIn(flag_key="a", variant="on", hour=START, count=3)],
    )

    sink.record_counts(ENV_A, [("a", "on"), ("a", "on")])
    await sink.store(ENV_A, body, START)
    persist["value"] = True
    await sink.store(ENV_A, EventsIn(exposures=body.exposures), START)
    buffer.seal()

    found = batches(tmp_path)
    [counts] = [b.counts for b in found if b.counts]
    assert [(c.flag_key, c.variant, c.hour, c.count) for c in counts] == [("a", "on", HOUR, 5)]
    [exposures] = [b.exposures for b in found if b.exposures]
    assert [e.context for e in exposures] == [None, {"user": "u1"}]
    assert all(e.timestamp == START for e in exposures)
    assert {b.environment_id for b in found} == {ENV_A}

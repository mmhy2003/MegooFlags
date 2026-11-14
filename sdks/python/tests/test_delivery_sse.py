import io
from collections.abc import Iterable
from dataclasses import dataclass, field

import pytest

from megooflags._delivery.sse import SseEvent, read_sse


class Chunks(io.RawIOBase):
    """A raw stream returning the given chunks one read at a time, like a socket."""

    def __init__(self, parts: Iterable[bytes | str], error: Exception | None = None) -> None:
        self._parts = [p.encode() if isinstance(p, str) else p for p in parts]
        self._error = error

    def readable(self) -> bool:
        return True

    def readinto(self, b: bytearray | memoryview) -> int:  # type: ignore[override]
        if not self._parts:
            if self._error is not None:
                raise self._error
            return 0
        part = self._parts[0]
        n = min(len(b), len(part))
        b[:n] = part[:n]
        if n == len(part):
            self._parts.pop(0)
        else:
            self._parts[0] = part[n:]
        return n


def stream(*parts: bytes | str, error: Exception | None = None) -> io.BufferedReader:
    return io.BufferedReader(Chunks(parts, error))


@dataclass
class Seen:
    lines: int = 0
    retries: list[int] = field(default_factory=list)
    events: list[SseEvent] = field(default_factory=list)


def read(*parts: bytes | str) -> Seen:
    seen = Seen()

    def on_line() -> None:
        seen.lines += 1

    read_sse(
        stream(*parts), on_line=on_line, on_retry=seen.retries.append, on_event=seen.events.append
    )
    return seen


@pytest.mark.parametrize(
    ("text", "lines", "events"),
    [
        pytest.param(
            'event: put\nid: 42\ndata: {"flags":{}}\n\n',
            4,
            [SseEvent("put", '{"flags":{}}', "42")],
            id="named event with id",
        ),
        pytest.param("data: x\n\n", 2, [SseEvent("message", "x", "")], id="default name"),
        pytest.param(
            "data: a\ndata: b\ndata:c\n\n",
            4,
            [SseEvent("message", "a\nb\nc", "")],
            id="multi-line data",
        ),
        pytest.param(
            "event: put\r\nid: 7\r\ndata: x\r\n\r\n",
            4,
            [SseEvent("put", "x", "7")],
            id="CRLF endings",
        ),
        pytest.param(
            "event: mystery\ndata: 1\n\n",
            3,
            [SseEvent("mystery", "1", "")],
            id="unknown event name passed through",
        ),
        pytest.param(
            "data: a\n\nevent: put\ndata: b\n",
            4,
            [SseEvent("message", "a", "")],
            id="unterminated last event discarded",
        ),
        pytest.param(
            "data: a\n\ndata: b",
            2,
            [SseEvent("message", "a", "")],
            id="unterminated last line discarded",
        ),
        pytest.param(
            "event: put\nid: 1\ndata: a\n\ndata: b\n\n",
            6,
            [SseEvent("put", "a", "1"), SseEvent("message", "b", "")],
            id="state resets after dispatch",
        ),
        pytest.param(
            "event: put\n\ndata: a\n\n",
            4,
            [SseEvent("message", "a", "")],
            id="empty line without data dispatches nothing",
        ),
        pytest.param(
            "id: a\0b\ndata: x\n\n",
            3,
            [SseEvent("message", "x", "")],
            id="id with NUL ignored",
        ),
        pytest.param(
            "data\ndata\n\n",
            3,
            [SseEvent("message", "\n", "")],
            id="field without colon has an empty value",
        ),
        pytest.param(
            "foo: bar\ndata:  two\n\n",
            3,
            [SseEvent("message", " two", "")],
            id="only one leading space removed, unknown field ignored",
        ),
        pytest.param(
            "data: a: b\n\n", 2, [SseEvent("message", "a: b", "")], id="colon inside the value kept"
        ),
    ],
)
def test_read_sse(text: str, lines: int, events: list[SseEvent]) -> None:
    seen = read(text)
    assert seen.events == events
    assert seen.lines == lines
    assert seen.retries == []


def test_comments_call_on_line_only() -> None:
    assert read(": ping\n:\n\n") == Seen(lines=3)


def test_a_valid_retry_calls_on_retry_in_milliseconds() -> None:
    assert read("retry: 3000\n\n") == Seen(lines=2, retries=[3000])


@pytest.mark.parametrize(
    "text", ["retry: x\n", "retry: -1\n", "retry: 1.5\n", "retry:\n", "retry: ３\n"]
)
def test_an_invalid_retry_is_ignored(text: str) -> None:
    assert read(text).retries == []


def test_reads_a_1_mib_data_line() -> None:
    big = "x" * (1 << 20)
    parts = ["event: put\ndata: "] + [big[i : i + 16_384] for i in range(0, len(big), 16_384)]
    seen = read(*parts, "\n\n")
    assert len(seen.events) == 1
    assert seen.events[0].data == big


def test_decodes_a_multi_byte_character_split_across_two_chunks() -> None:
    data = "data: é€😀\n\n".encode()
    at = data.index(0xE2) + 1
    assert read(data[:at], data[at:]).events == [SseEvent("message", "é€😀", "")]


def test_splits_lines_that_arrive_in_one_chunk_or_byte_by_byte() -> None:
    text = b"event: put\r\ndata: a\r\n\r\nevent: put\ndata: b\n\n"
    one = read(text)
    split = read(*(bytes([b]) for b in text))
    assert split == one
    assert [e.data for e in one.events] == ["a", "b"]


def test_an_error_raised_by_on_event_stops_reading() -> None:
    names: list[str] = []

    class Stop(Exception):
        pass

    def on_event(e: SseEvent) -> None:
        names.append(e.name)
        raise Stop

    with pytest.raises(Stop):
        read_sse(
            stream("event: revoked\ndata: {}\n\nevent: put\ndata: x\n\n"),
            on_line=lambda: None,
            on_retry=lambda _: None,
            on_event=on_event,
        )
    assert names == ["revoked"]


def test_a_read_error_is_passed_through() -> None:
    events: list[SseEvent] = []
    with pytest.raises(ConnectionResetError):
        read_sse(
            stream("data: a\n\n", error=ConnectionResetError("reset")),
            on_line=lambda: None,
            on_retry=lambda _: None,
            on_event=events.append,
        )
    assert events == [SseEvent("message", "a", "")]


def test_reads_a_live_http_response() -> None:
    from fake_server import FakeServer, Stream

    from megooflags._transport import build_headers, open_request, sdk_url

    with FakeServer() as server:
        server.reply("/sdk/v1/stream", Stream())
        conn, resp = open_request(
            "GET", sdk_url(server.url, "stream"), headers=build_headers("k", stream=True), timeout=5
        )
        try:
            sse = server.next_stream()
            sse.write("event: put\ndata: {}\n\n")
            sse.write(": ping\n")
            sse.end()
            seen = Seen()
            read_sse(
                resp,
                on_line=lambda: None,
                on_retry=seen.retries.append,
                on_event=seen.events.append,
            )
            assert seen.events == [SseEvent("put", "{}", "")]
        finally:
            conn.close()

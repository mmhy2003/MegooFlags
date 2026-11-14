"""A text/event-stream parser over a binary stream (the HTTP response)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class SseEvent:
    """One dispatched server-sent event. name is "message" by default."""

    name: str
    data: str
    id: str


class LineReader(Protocol):
    """A binary stream that can read up to and including the next LF byte."""

    def readline(self, size: int | None = -1, /) -> bytes: ...


def read_sse(
    stream: LineReader,
    *,
    on_line: Callable[[], None],
    on_retry: Callable[[int], None],
    on_event: Callable[[SseEvent], None],
) -> None:
    """Parse events until the stream ends. on_line runs for every line, comments included;
    on_retry receives a valid retry field in milliseconds; on_event receives each event.

    Lines are split on the LF byte before decoding UTF-8, so a character split across reads
    decodes whole, and a line of any length is accepted. An unterminated last line or event is
    discarded. A read error, or an exception raised by a callback, propagates unchanged."""
    name = ""
    event_id = ""
    data: list[str] = []
    seen = False
    while True:
        raw = stream.readline()
        if not raw.endswith(b"\n"):
            return
        raw = raw[:-2] if raw.endswith(b"\r\n") else raw[:-1]
        line = raw.decode("utf-8", errors="replace")
        on_line()
        if line == "":
            if seen:
                event = SseEvent(name or "message", "\n".join(data), event_id)
                name, event_id, data, seen = "", "", [], False
                on_event(event)
            else:
                name, event_id = "", ""
            continue
        if line.startswith(":"):
            continue
        field, colon, value = line.partition(":")
        if colon and value.startswith(" "):
            value = value[1:]
        if field == "event":
            name = value
        elif field == "data":
            data.append(value)
            seen = True
        elif field == "id":
            if "\0" not in value:
                event_id = value
        elif field == "retry":
            if value.isascii() and value.isdigit():
                on_retry(int(value))

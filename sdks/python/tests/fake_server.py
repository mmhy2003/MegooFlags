"""A scripted HTTP server on 127.0.0.1 for the transport, delivery, events and provider tests."""

from __future__ import annotations

import queue
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit


@dataclass(frozen=True)
class Scripted:
    """A canned response: status, headers and body."""

    status: int
    headers: dict[str, str] = field(default_factory=dict)
    body: bytes | str = b""


@dataclass(frozen=True)
class Stream:
    """A response held open as text/event-stream; the test feeds it through an SseConnection."""

    status: int = 200
    headers: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Received:
    """A request the server received, with its whole body. at is time.time() on arrival."""

    method: str
    path: str
    url: str
    headers: dict[str, str]
    body: bytes
    at: float


# A function answering a request, possibly after a delay (it runs on the request's thread).
Responder = Callable[[Received], Scripted]
Reply = Scripted | Stream | Responder

_END = object()
_DROP = object()


class SseConnection:
    """An open event-stream response, written from the test through a queue."""

    def __init__(self, request: Received) -> None:
        self.request = request
        self.closed = threading.Event()
        self._queue: queue.Queue[object] = queue.Queue()

    def write(self, data: str | bytes) -> None:
        """write sends data as is."""
        self._queue.put(data.encode() if isinstance(data, str) else data)

    def end(self) -> None:
        """end finishes the response and closes the connection."""
        self._queue.put(_END)

    def drop(self) -> None:
        """drop closes the connection without finishing the response."""
        self._queue.put(_DROP)


class FakeServer:
    """FakeServer answers each path from its queue of replies. The last reply of a queue repeats,
    so one reply serves every request; a path without replies gets 404. Use it as a context
    manager, or call close()."""

    def __init__(self) -> None:
        self.requests: list[Received] = []
        self.streams: list[SseConnection] = []
        self._routes: dict[str, list[Reply]] = {}
        self._lock = threading.Lock()
        self._stream_ready = threading.Condition(self._lock)
        self._taken = 0
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler_class())
        self._server.daemon_threads = True
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def __enter__(self) -> FakeServer:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    @property
    def url(self) -> str:
        """url is the base URL, for example http://127.0.0.1:41234."""
        host, port = self._server.server_address[:2]
        return f"http://{host!s}:{port}"

    def reply(self, path: str, *replies: Reply) -> None:
        """reply adds replies to the queue of path."""
        with self._lock:
            self._routes.setdefault(path, []).extend(replies)

    def received(self, path: str) -> list[Received]:
        """received lists the requests for path."""
        with self._lock:
            return [r for r in self.requests if r.path == path]

    def next_stream(self, timeout: float = 5.0) -> SseConnection:
        """next_stream returns the first event-stream connection not yet handed out, waiting for
        it up to timeout seconds."""
        with self._stream_ready:
            if not self._stream_ready.wait_for(lambda: len(self.streams) > self._taken, timeout):
                raise TimeoutError("no stream connection")
            conn = self.streams[self._taken]
            self._taken += 1
            return conn

    def close(self) -> None:
        """close ends every open stream, stops the listener and waits for it."""
        with self._lock:
            streams = list(self.streams)
        for s in streams:
            s.drop()
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(5)

    def _next_reply(self, path: str) -> Reply | None:
        with self._lock:
            replies = self._routes.get(path)
            if not replies:
                return None
            return replies.pop(0) if len(replies) > 1 else replies[0]

    def _handler_class(self) -> type[BaseHTTPRequestHandler]:
        server = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                self._handle()

            def do_POST(self) -> None:
                self._handle()

            def log_message(self, format: str, *args: object) -> None:
                pass

            def _handle(self) -> None:
                at = time.time()
                length = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(length) if length else b""
                req = Received(
                    method=self.command,
                    path=urlsplit(self.path).path,
                    url=self.path,
                    headers={k.lower(): v for k, v in self.headers.items()},
                    body=body,
                    at=at,
                )
                with server._lock:
                    server.requests.append(req)
                reply = server._next_reply(req.path)
                if reply is None:
                    self._send(Scripted(404))
                elif isinstance(reply, Stream):
                    self._stream(req, reply)
                elif isinstance(reply, Scripted):
                    self._send(reply)
                else:
                    self._send(reply(req))

            def _send(self, r: Scripted) -> None:
                body = r.body.encode() if isinstance(r.body, str) else r.body
                try:
                    self.send_response(r.status)
                    for k, v in r.headers.items():
                        self.send_header(k, v)
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                except OSError:
                    pass

            def _stream(self, req: Received, r: Stream) -> None:
                conn = SseConnection(req)
                try:
                    self.send_response(r.status)
                    self.send_header("Content-Type", "text/event-stream")
                    self.send_header("Cache-Control", "no-cache")
                    for k, v in r.headers.items():
                        self.send_header(k, v)
                    self.end_headers()
                    self.wfile.flush()
                    with server._stream_ready:
                        server.streams.append(conn)
                        server._stream_ready.notify_all()
                    while True:
                        item = conn._queue.get()
                        if item is _END or item is _DROP:
                            break
                        assert isinstance(item, bytes)
                        self.wfile.write(item)
                        self.wfile.flush()
                except OSError:
                    pass
                finally:
                    self.close_connection = True
                    conn.closed.set()

        return Handler
